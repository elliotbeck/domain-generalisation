import argparse
import copy
import datetime
import json
import pickle
import math
import os
import random
from random import shuffle
import matplotlib
import sklearn
import itertools
import copy

#matplotlib.use('tkAgg')
import matplotlib.pyplot as plt
import tensorflow_datasets as tfds

from models import get_model, MetaReg
from util import copy_source

plt.interactive(False)

from absl import flags, app, logging
import tensorflow as tf
#import tensorflow_transform as tft
import numpy as np
import time
import experiment_repo as repo

import util
import local_settings
from collections import defaultdict

DEBUG = True


parser = argparse.ArgumentParser(description='Train my model.')
parser.add_argument('--config', type=str, 
    default="configs/config_class_metareg.json",
    help='Path to config file.')
parser.add_argument('--all_checkpoints_folder', type=str, 
    default="checkpoints_pretr", help='Checkpoint folder name.')
parser.add_argument('--reload_ckpt', type=str, default="None", 
    help='Run ID from which to continue training.')
parser.add_argument('--local_json_dir_name', type=str,
    help='Folder name to save results jsons.')  
parser.add_argument('--dataset', type=str, help='Dataset.')
parser.add_argument('--name', type=str, help='Model name.')
parser.add_argument('--learning_rate', type=float, help='Learning rate.') 
parser.add_argument('--batch_size', type=int, help='Batch size.')
parser.add_argument('--num_epochs', type=int, help='Number of epochs.')
parser.add_argument('--decay_every', type=float, help='Decay steps.')
parser.add_argument('--img_size', type=int, help='Number of epochs.')
parser.add_argument('--l2_penalty_weight', type=float, help='L2 penalty weight.')
parser.add_argument('--validation_size', type=int, help='validation set size.')
parser.add_argument('--overwrite_configs', type=int, 
    help='Flag whether to overwrite configs.')
parser.add_argument('--dropout_rate', type=float, help='Dropout rate.')
parser.add_argument('--use_dropout', type=int, help='Flag whether to use dropout.')
parser.add_argument('--alpha', type=float, help='weighting factor of classification loss.')
parser.add_argument('--lambda', type=float, help='weighting factor of generator.')

# this is the loss function used in the paper without regularizer
def loss_fn_regular(features1, features2,  model_task1, model_task2, 
                    config, training):
    inputs1 = features1["image"]
    label1 = tf.squeeze(features1["label"])
    inputs2 = features2["image"]
    label2 = tf.squeeze(features2["label"])

    # predict the outputs of all task networks (one per domain)
    model_task1_output = model_task1(inputs1, training=training)
    model_task2_output = model_task2(inputs2, training=training)

    # calculate the mean loss of all task networks (one per domain)
    model_task1_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels = tf.one_hot(label1, axis=-1, 
                                depth=config.num_classes_label), 
                                logits = model_task1_output), name='Label_loss1')
    model_task2_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels = tf.one_hot(label2, axis=-1, 
                                depth=config.num_classes_label), 
                                logits = model_task2_output), name='Label_loss2')     

    # total mean loss over all task networks (one per domain)
    label_loss = tf.reduce_mean([model_task1_loss, model_task2_loss])

    # calculate the accuracies of all task networks
    accuracy1 = tf.reduce_mean(
        tf.where(tf.equal(label1, tf.argmax(model_task1_output, axis=-1)),
                    tf.ones_like(label1, dtype=tf.float32),
                    tf.zeros_like(label1, dtype=tf.float32)))

    accuracy2 = tf.reduce_mean(
        tf.where(tf.equal(label2, tf.argmax(model_task2_output, axis=-1)),
                    tf.ones_like(label2, dtype=tf.float32),
                    tf.zeros_like(label2, dtype=tf.float32)))
    

    # get the mean accurary over all task models
    accuracy = tf.reduce_mean([accuracy1, accuracy2])

    return label_loss, model_task1_loss, model_task2_loss, accuracy

# this is the loss function used in the paper with regularizer, used for training after metalearning
def loss_fn_full(features1, features2, model_final, model_regularizer, config, training):
    inputs1 = features1["image"]
    label1 = tf.squeeze(features1["label"])
    inputs2 = features2["image"]
    label2 = tf.squeeze(features2["label"])
    inputs = tf.concat([inputs1, inputs2], 0)
    label = tf.concat([label1, label2], 0)

    # predict the labels  
    model_final_output = model_final(inputs, training=training)

    # get the loss of the classifier model
    model_final_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels = tf.one_hot(label, axis=-1, 
                                depth=config.num_classes_label), 
                                logits = model_final_output))

    # reshape loss due to requirements of equivalent shape of elements in reduce_mean
    model_final_loss = tf.reshape(model_final_loss, [1,1])

    # get the weights of the last layer in the right format
    last_layer = tf.expand_dims(tf.abs(tf.reshape(model_final.model.layers[-1].trainable_variables[0], [-1])), 0)

    # calculate the regularizer loss as via the regularizer NN
    regularizer_loss = model_regularizer(last_layer)

    # get the mean total loss
    total_loss = tf.reduce_sum([model_final_loss, regularizer_loss])

    #calculate the accuracies
    accuracy = tf.math.reduce_mean(tf.where(tf.equal(label, 
                    tf.argmax(model_final_output, axis=-1)),
                    tf.ones_like(label, dtype=tf.float64),
                    tf.zeros_like(label, dtype=tf.float64)))

    return total_loss, accuracy
# first step of metalearning, regular backpropagation
def _train_step1(model_task1, model_task2, features1, features2, 
                optimizer, global_step, config):
    
    with tf.GradientTape() as tape_src:

        # get loss of all networks 
        _, model_task1_loss, model_task2_loss, _ = loss_fn_regular(features1, 
                                    features2, model_task1, model_task2, config=config, training=True)

        # calculate gradients and apply SGD updates
        grads1 = tape_src.gradient(model_task1_loss, model_task1.trainable_variables)
        optimizer.apply_gradients(zip(grads1, model_task1.trainable_variables))

    with tf.GradientTape() as tape_src:
        
        # get loss of all networks 
        _, model_task1_loss, model_task2_loss,  _ = loss_fn_regular(features1, 
                                    features2, model_task1, model_task2, config=config, training=True)
        # calculate gradients and apply SGD updates
        grads2 = tape_src.gradient(model_task2_loss, model_task2.trainable_variables)
        optimizer.apply_gradients(zip(grads2, model_task2.model.trainable_variables))

# Second step of metalearning, episodic training
def _train_step2(model1, model2, model_regularizer, features1, features2, 
                optimizer, global_step, config, models, random_domains):

    with tf.GradientTape() as tape_src:
        _, model1_loss, model2_loss, _ = loss_fn_regular(features1, 
                                    features2,  model1, model2, config=config, training=True)
    
        loss = [model1_loss, model2_loss]

        meta_train_loss = loss[random_domains[0]]
        meta_train_model = models[random_domains[0]]

        # calculate gradients and apply SGD updates
        grads = tape_src.gradient(meta_train_loss, meta_train_model.trainable_variables) 
        optimizer.apply_gradients(zip(grads, meta_train_model.trainable_variables))

    with tf.GradientTape() as tape_src:
        output = model_regularizer(tf.expand_dims(tf.abs(tf.reshape(meta_train_model.trainable_variables[0], [-1])), 0))
        # calculate gradients and apply SGD updates
        grads = tape_src.gradient(output, model_regularizer.trainable_variables) 
        grads = tf.reshape(grads, [256,2])
        optimizer.apply_gradients(zip([grads], [meta_train_model.trainable_variables[0]]))

# # third step of metalearning, update regularizer NN
# def _train_step3(model_regularizer, features1, features2, features3, 
#                 optimizer, global_step, config, models ,random_domains):

#     meta_test_model = models[random_domains[0]]

#     _, model1_loss, model2_loss, model3_loss, _ = loss_fn_regular(features1, features2, features3, 
#                                                         meta_test_model, meta_test_model, meta_test_model, 
#                                                         config=config, training=True)

#     loss = [model1_loss, model2_loss, model3_loss]
#     meta_test_loss = loss[random_domains[1]]

#     with tf.GradientTape() as tape_src:

#         loss = [model1_loss, model2_loss, model3_loss]
#         meta_test_loss = loss[random_domains[1]]
#         # calculate gradients and apply SGD updates
#         grads = tape_src.gradient(meta_test_loss, model_regularizer.trainable_variables)
#         print(grads) 
#         optimizer.apply_gradients(zip(grads, model_regularizer.trainable_variables))



# train one epoch of the metalearning (not train the full model)
def train_one_epoch(model_task1, model_task2, model1, 
                    model2, model_regularizer, train_input1, 
                    train_input2, optimizer, global_step, config):

    # randomly shuffle data before each epoch
    train_input1 = train_input1.shuffle(buffer_size=10000)
    train_input2 = train_input2.shuffle(buffer_size=10000)


    # TRAIN_STEP1, regular training (first part of MetaReg algo)
    for _input1, _input2, in zip(train_input1, train_input2):
        _train_step1(model_task1, model_task2,  _input1, _input2,
                        optimizer, global_step, config)
    
    # sample two random domains
    random_domains = random.sample([0, 1], 2)

    # all layers but the last untrainable (needed for meta learning)
    for layer in model_task1.model.layers[:-1]:
        layer.trainable = False 
    for layer in model_task2.model.layers[:-1]:
        layer.trainable = False
    
    # not possible to use deepcopy, thus for loop workaround
    for a, b in zip(model1.variables, model_task1.variables):
        a.assign(b)
    for a, b in zip(model2.variables, model_task2.variables):
        a.assign(b)
    models = [model1, model2]
    
    # only take l(=20) batches of the datasets
    train_input1 = train_input1.take(20)
    train_input2 = train_input2.take(20)

    # TRAIN_STEP2, meta learning of regularizer
    for _input1, _input2 in zip(train_input1, train_input2):
        _train_step2(model1, model2, model_regularizer, _input1, _input2,  optimizer, 
        global_step, config, models=models, random_domains=random_domains)
    
    # all layers trainable again (unset meta learning)
    for layer in model_task1.model.layers[:-1]:
        layer.trainable = True 
    for layer in model_task2.model.layers[:-1]:
        layer.trainable = True

    # # TRAIN_STEP3, meta update for regularizer
    # for _input1, _input2, _input3 in zip(train_input1, train_input2, train_input3):
    #     _train_step3(model_regularizer, _input1, _input2, _input3, optimizer, global_step, config, 
    #                     models=models, random_domains=random_domains)


def _train_step_full(features1, features2, model_final,
                        model_regularizer, optimizer, global_step, config):
    with tf.GradientTape() as tape_src:
        # get loss of classifier
        total_loss, accuracy = loss_fn_full(features1, features2, model_final,
                                            model_regularizer, config, training=True)

        tf.summary.scalar("binary_crossentropy", total_loss, 
            step=global_step)
        tf.summary.scalar("accuracy", accuracy, step=global_step)

        # update weights of critic
        grads = tape_src.gradient(total_loss, model_final.trainable_variables)
        optimizer.apply_gradients(zip(grads, model_final.trainable_variables))
        
        global_step.assign_add(1)

# train the full model one epoch after metalearning
def train_one_epoch_full(train_input1, train_input2, model_final,
                            model_regularizer, optimizer, global_step, config):

    # freeze regularizer as stated in paper
    for layer in model_regularizer.model.layers[:]:
        layer.trainable = False 

    # train one batch on final model
    for _input1, _input2 in zip(train_input1, train_input2):
        _train_step_full(_input1, _input2, model_final,
                            model_regularizer, optimizer, global_step, config)
    

# compute the mean of all examples for a specific set (eval, validation, out-of-distribution, etc)
def eval_one_epoch(model_final, model_regularizer, dataset, summary_directory, global_step, config, training):
    classification_loss = tf.metrics.Mean("binary_crossentropy")
    accuracy = tf.metrics.Mean("accuracy")

    dataset1, dataset2 = dataset.shard(2, 0), dataset.shard(2, 1)
    # losses = []
    # accuracies = []
    for _input1, _input2 in zip(dataset1,dataset2):
        _classification_loss, _accuracy = loss_fn_full(features1=_input1, features2=_input2, 
                                                    model_final = model_final, model_regularizer = model_regularizer, 
                                                    config=config, training=training)
        # losses.append(_classification_loss.numpy())
        # accuracies.append(_accuracy.numpy())

        # update mean-metric
        classification_loss(_classification_loss)
        accuracy(_accuracy)

    writer = tf.summary.create_file_writer(summary_directory)
    with writer.as_default(), tf.summary.record_if(True):
        tf.summary.scalar("classification_loss", classification_loss.result(), 
            step=global_step)
        tf.summary.scalar("accuracy", accuracy.result(), step=global_step)

    results_dict = {"accuracy": accuracy.result(), 
        "loss": classification_loss.result()}

    return results_dict


def _preprocess_exampe(model_label, example, dataset_name, domain, e):
    example["image"] = tf.cast(example["image"], dtype=tf.float64)/255.
    # 2x subsample for computational convenience
    example["image"] = example["image"][::2, ::2, :]
    example["image"] = tf.squeeze(example["image"], axis=-1)
    # Assign a binary label based on the digit; flip label with probability 0.25
    label = tf.cast([[example["label"] < 5]], dtype=tf.int64)
    label = util.tf_xor(label, tf.cast(util.tf_bernoulli(0.25, 1), dtype=tf.int64))
    # Assign a color based on the label; flip the color with probability e
    colors = util.tf_xor(label, tf.cast(util.tf_bernoulli(e, 1), dtype=tf.int64))
    re_colors = 1-colors
    re_colors = tf.cast(re_colors, dtype=tf.int32)
    # Apply the color to the image by zeroing out the other color channel
    if re_colors == tf.constant(0): 
        image = tf.stack([tf.zeros([14,14], dtype=tf.float64),
        example["image"]], axis=-1)
    else: 
        image = tf.stack([example["image"], tf.zeros([14,14], 
        dtype=tf.float64)], axis=-1)
    example["image"] = image
    example["label"] = label
    example["domain"] = domain

    return example  


def _get_dataset(dataset_name, model_label, split, batch_size, e, domain,
    num_batches=None):

    dataset, info = tfds.load(dataset_name, data_dir=local_settings.TF_DATASET_PATH, 
        split=split, with_info=True)
    dataset = dataset.map(lambda x: _preprocess_exampe(model_label, x, dataset_name, domain, e))
    dataset = dataset.shuffle(512)
    dataset = dataset.batch(batch_size)
    dataset = dataset.take(tf.cast(20, tf.int64))
    if num_batches is not None:
        dataset = dataset.take(num_batches)

    # dataset = dataset.prefetch(2)

    return dataset


def main():
    # parse args and get configs
    args = parser.parse_args()
    logging.set_verbosity(logging.INFO)

    # reload model from checkpoint or train from scratch
    if args.reload_ckpt != "None":
        checkpoint_path = os.path.join(local_settings.MODEL_PATH, 
            args.all_checkpoints_folder)
        checkpoint_folders = os.listdir(checkpoint_path)
        checkpoint_folder = [f for f in checkpoint_folders if args.reload_ckpt in f]
        if len(checkpoint_folder) == 0:
            raise Exception("No matching folder found.")
        elif len(checkpoint_folder) > 1:
            logging.info(checkpoint_folder)
            raise Exception("More than one matching folder found.")
        else:
            checkpoint_folder = checkpoint_folder[0]
            logging.info("Restoring from {}".format(checkpoint_folder))
        checkpoint_dir = os.path.join(checkpoint_path, checkpoint_folder)
        
        if not args.overwrite_configs:
            # reload configs from file
            with open(os.path.join(checkpoint_dir, "hparams.pkl"), 'rb') as f:
                config_dict = pickle.load(f)
        else:
            # get configs
            config_dict = util.get_config(args.config)
            config_dict = util.update_config(config_dict, args)
    else:
        # get configs
        config_dict = util.get_config(args.config)
        config_dict = util.update_config(config_dict, args)

    config_dict_copy = copy.deepcopy(config_dict)
    config = util.config_to_namedtuple(config_dict)

    # Initialize the repo
    logging.info("==> Creating repo..")
    exp_repo = repo.ExperimentRepo(local_dir_name=config.local_json_dir_name,
        root_dir=local_settings.MODEL_PATH)

    if args.reload_ckpt != "None":
        exp_id = config_dict["id"]
    else:
        exp_id = None
    
    # Create new experiment
    exp_id = exp_repo.create_new_experiment(config.dataset, 
        config_dict_copy, exp_id)
    config_dict_copy["id"] = exp_id

    # Set up model directory
    current_time = datetime.datetime.now().strftime(r"%y%m%d_%H%M")
    ckpt_dir_name = args.all_checkpoints_folder if not DEBUG else 'checkpoints_tmp'
    ckpt_dir = os.path.join(local_settings.MODEL_PATH, ckpt_dir_name)
    os.makedirs(ckpt_dir, exist_ok=True)
    if args.reload_ckpt != "None":
        model_dir = checkpoint_dir
    else:
        model_dir = os.path.join(
            ckpt_dir, "ckpt_{}_{}".format(current_time, exp_id))
    
    # Save hyperparameter settings
    os.makedirs(model_dir, exist_ok=True)
    if not os.path.exists(os.path.join(model_dir, "hparams.json")):
        with open(os.path.join(model_dir, "hparams.json"), 'w') as f:
            json.dump(config_dict_copy, f, indent=2, sort_keys=True)
        with open(os.path.join(model_dir, "hparams.pkl"), 'wb') as f:
            pickle.dump(config_dict_copy, f)

    # Set optimizers
    # learning_rate = tf.keras.optimizers.schedules.ExponentialDecay(
    #     config.learning_rate, config.decay_every, 
    #     config.decay_base, staircase=True)
    # learning rate = 0.02 in paper
    optimizer = tf.keras.optimizers.SGD(learning_rate=0.001, momentum=0.9)




    if args.reload_ckpt != "None":
        # TODO: fix this hack
        epoch_start = int(sorted([f for f in os.listdir(checkpoint_dir) 
            if 'ckpt-' in f])[-1].split('ckpt-')[1].split('.')[0])
        init_gs = 0
    else:
        epoch_start = 0
        init_gs = 0

    global_step = tf.Variable(initial_value=init_gs, name="global_step", 
        trainable=False, dtype=tf.int64)

    # Get model
    model_feature = get_model(config.name_feature_network, config)
    model_task1 = get_model(config.name_classifier_task, config, model_feature = model_feature)
    model1 = get_model(config.name_classifier_task, config, model_feature = model_feature)
    model_task2 = get_model(config.name_classifier_task, config, model_feature = model_feature)
    model2 = get_model(config.name_classifier_task, config, model_feature = model_feature)
    model_regularizer = get_model(config.name_regularizer_network, config)

    model_final = get_model(config.name_classifier_task, config, model_feature = model_feature)


    # Get datasets
    if DEBUG:
        num_batches = 5
    else:
        num_batches = None

    ds_train1 = _get_dataset(config.dataset, model_task1, 
        split=tfds.Split.TRAIN.subsplit(tfds.percent[:50]), 
        batch_size=tf.cast(config.batch_size/2, tf.int64), 
        num_batches=num_batches, domain = tf.constant(0), e = 0.2)

    ds_train2 = _get_dataset(config.dataset, model_task1, 
        split=tfds.Split.TRAIN.subsplit(tfds.percent[-50:]), 
        batch_size=tf.cast(config.batch_size/2, tf.int64),
        num_batches=num_batches, domain = tf.constant(1), e = 0.1)
    
    ds_val = _get_dataset(config.dataset, model_task1, 
        split=tfds.Split.TEST, batch_size=config.batch_size, 
        num_batches=num_batches, domain = tf.constant(2), e = 0.9)



    # Set up checkpointing
    if args.reload_ckpt != "None":
        ckpt = tf.train.Checkpoint(model=model_task1, global_step=global_step)
        manager = tf.train.CheckpointManager(ckpt, checkpoint_dir, max_to_keep=3)
        status = ckpt.restore(manager.latest_checkpoint)
        status.assert_consumed()
    else:
        ckpt = tf.train.Checkpoint(model=model_task1, global_step=global_step)
        manager = tf.train.CheckpointManager(ckpt, model_dir, max_to_keep=3) 

    writer = tf.summary.create_file_writer(manager._directory)
    with writer.as_default(), tf.summary.record_if(lambda: int(global_step.numpy()) % 100 == 0):
        for epoch in range(epoch_start, config.num_epochs):
        
            start_time = time.time()

            # Metalearning of the regularizer
            if epoch < (config.num_epochs/2):
                train_one_epoch(model_task1 = model_task1, model_task2=model_task2, 
                    model1 = model1, model2 = model2,
                    model_regularizer=model_regularizer, train_input1=ds_train1, 
                    train_input2=ds_train2, optimizer=optimizer,
                    global_step=global_step, config=config)

            if epoch >= (config.num_epochs/2):
                
                # After Metalearning, train the full model
                train_one_epoch_full(train_input1=ds_train1, train_input2=ds_train2, 
                                    model_final=model_final, 
                                    model_regularizer=model_regularizer, optimizer=optimizer,
                                    global_step=global_step, config=config)
                    
                train1_metr = eval_one_epoch(model_final=model_final, 
                    model_regularizer=model_regularizer, dataset=ds_train1,
                    summary_directory=os.path.join(manager._directory, "train1"), 
                    global_step=global_step, config=config, training=False)
                
                train2_metr = eval_one_epoch(model_final=model_final, 
                    model_regularizer=model_regularizer, dataset=ds_train2,
                    summary_directory=os.path.join(manager._directory, "train2"), 
                    global_step=global_step, config=config, training=False)

                val_metr = eval_one_epoch(model_final=model_final, 
                    model_regularizer=model_regularizer, dataset=ds_val,
                    summary_directory=os.path.join(manager._directory, "val"),
                    global_step=global_step, config=config, training=False)
            


                manager.save()

                logging.info("\n #### \n epoch: %d, time: %0.2f" % 
                    (epoch, time.time() - start_time))
                logging.info("Global step: {}".format(global_step.numpy()))
                logging.info("train1_accuracy: {:2f}, train1_loss: {:4f}".format(
                    train1_metr['accuracy'], train1_metr['loss']))
                logging.info("train2_accuracy: {:2f}, train2_loss: {:4f}".format(
                    train2_metr['accuracy'], train2_metr['loss']))                
                logging.info("val_accuracy: {:2f}, val_loss: {:4f}".format(
                    val_metr['accuracy'], val_metr['loss']))

            if epoch == epoch_start:
                dir_path = os.path.dirname(os.path.realpath(__file__))
                copy_source(dir_path, manager._directory)

    
    # Mark experiment as completed
    # TODO: add other metrics - done
    exp_repo.mark_experiment_as_completed(exp_id, 
        train1_accuracy=train1_metr['accuracy'],
        train2_accuracy=train2_metr['accuracy'],
        val_accuracy=val_metr['accuracy'])

if __name__ == "__main__":
    main()