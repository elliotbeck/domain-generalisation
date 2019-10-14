import argparse
import copy
import datetime
import json
import pickle
import math
import os
from random import shuffle
import matplotlib
import sklearn

#matplotlib.use('tkAgg')
import matplotlib.pyplot as plt
import tensorflow_datasets as tfds

from models import get_model
from util import copy_source

plt.interactive(False)

from absl import flags, app, logging
import tensorflow as tf
import numpy as np
import time
from datasets import pacs
import experiment_repo as repo

import util
import local_settings

DEBUG = True

parser = argparse.ArgumentParser(description='Train my model.')
parser.add_argument('--config', type=str, 
    default="configs/config_class.json",
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

# loss funtion for classifier
def loss_fn_classifier(model_classifier, model_generator, features, config, training):
    inputs = features["image"]
    label = tf.squeeze(features["label"])
    inputs_generated = model_generator(inputs, training=training)
    label_generated = label
    #inputs_all = tf.concat([inputs, inputs_generated], 0)
    label_all = tf.concat([label, label_generated], 0)

    # L2 regularizers
    l2_regularizer = tf.add_n([tf.nn.l2_loss(v) for v in 
        model_classifier.trainable_variables if 'bias' not in v.name])
    # get label predictions
    model_classifier_output_original = model_classifier(inputs, training=training)
    model_classifier_output_generated = model_classifier(inputs_generated, 
                                            training=training)
    # get mean classification loss on original data                                        
    classification_loss_original = tf.losses.binary_crossentropy(
        tf.one_hot(label, axis=-1, depth=config.num_classes),
        model_classifier_output_original, from_logits=False)
    mean_classification_loss_original = tf.reduce_mean(classification_loss_original)
    # get mean classification loss on generated data
    classification_loss_generated = tf.losses.binary_crossentropy(
        tf.one_hot(label_generated, axis=-1, depth=config.num_classes),
        model_classifier_output_generated, from_logits=False)
    mean_classification_loss_generated = tf.reduce_mean(classification_loss_generated)
    # get weighted total loss
    mean_classification_loss_weighted = (1-config.alpha) * mean_classification_loss_original + \
        config.alpha * mean_classification_loss_generated
    # calculate accuracy 
    accuracy = tf.reduce_mean(
        tf.where(tf.equal(label_all, tf.argmax(tf.concat([model_classifier_output_original,
                    model_classifier_output_generated], 0), axis=-1)),
                    tf.ones_like(label_all, dtype=tf.float32),
                    tf.zeros_like(label_all, dtype=tf.float32)))

    return mean_classification_loss_weighted, l2_regularizer, accuracy, classification_loss

# loss function for generator

# loss function for critic
def loss_fn_critic(model_critic, model_generator, features, config, training):
    inputs = features["image"]
    label = tf.squeeze(features["label"])

    X_generated = model_generator(inputs, training=training)
    X_critic_true = model_critic(inputs, training=training)
    X_critic_generated = model_critic(X_generated, training=training)

    # compute M (cost_matrix)
    norms_true = tf.norm(X_critic_true,2, axis=1)
    norms_generated = tf.norm(X_critic_generated,2, axis=1)
    matrix_norms = tf.tensordot(norms_true,norms_generated, axes=0)
    matrix_critic = tf.tensordot(X_critic_true,X_critic_generated.T, axes=1)
    cost_matrix = 1 - matrix_critic/matrix_norms
    
    _, sinkhorn_dist = util.compute_optimal_transport(cost_matrix,?;??;)

    return sinkhorn_dist


def _train_step(model_classifier, features, optimizer, global_step, config):
    with tf.GradientTape() as tape_src:
        mean_classification_loss_weighted, l2_regularizer, accuracy, _ = loss_fn_classifier(
            model_classifier, model_generator ,features=features, config=config, training=True)

        tf.summary.scalar("binary_crossentropy", mean_classification_loss, 
            step=global_step)
        tf.summary.scalar("accuracy", accuracy, step=global_step)

        total_loss = mean_classification_loss_weighted + \
            config.l2_penalty_weight*l2_regularizer

        grads = tape_src.gradient(total_loss, model_classifier.trainable_variables)
        optimizer.apply_gradients(zip(grads, model_classifier.trainable_variables))

        global_step.assign_add(1)


# # choose two domains of train_input
# def predicate(x, allowed_domains=tf.constant(["cartoon", "sketch"])):
#     domain = x["domain"]
#     isallowed = tf.equal(allowed_domains, domain)
#     reduced = tf.reduce_sum(tf.cast(isallowed, tf.float32))
#     return tf.greater(reduced, tf.constant(0.))


def train_one_epoch(model_classifier, train_input, optimizer, global_step, config):

    # train_input = train_input.filter(lambda x: predicate(x))
    # print(train_input)

    for _input in train_input:
        _train_step(model, _input, optimizer, global_step, config)


# compute the mean of all examples for a specific set (eval, validation, out-of-distribution, etc)
def eval_one_epoch(model_classifier, model_generator, dataset, summary_directory, global_step, config, training):
    classification_loss = tf.metrics.Mean("binary_crossentropy")
    accuracy = tf.metrics.Mean("accuracy")

    # losses = []
    # accuracies = []
    for _input in dataset:
        _, _, _accuracy, _classification_loss = loss_fn_classifier(model_classifier, model_generator,
        features=_input, config=config, training=training)
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


def _preprocess_exampe(model_classifier, example, dataset_name):
    example["image"] = tf.cast(example["image"], dtype=tf.float32)/255.
    example["image"] = tf.image.resize(example["image"], 
        size=(model_classifier.input_shape[0], model_classifier.input_shape[1]))
    example["label"] = example["attributes"]["label"]
    example["domain"] = example["attributes"]["domain"]
    return example


def _get_dataset(dataset_name, model_classifier, validation_split, split, batch_size, 
    num_batches=None):

    builder_kwargs = {
        "validation_split": validation_split,
    }

    dataset, info = tfds.load(dataset_name, data_dir=local_settings.TF_DATASET_PATH, 
        split=split, builder_kwargs=builder_kwargs, with_info=True)
    dataset = dataset.map(lambda x: _preprocess_exampe(model_classifier, x, dataset_name))
    dataset = dataset.shuffle(512)
    dataset = dataset.batch(batch_size)
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
    learning_rate = tf.keras.optimizers.schedules.ExponentialDecay(
        config.learning_rate, config.decay_every, 
        config.decay_base, staircase=True)
    optimizer = tf.keras.optimizers.Adam(learning_rate)
    
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
    model_classifier = get_model(config.name_classifier, config)
    model_critic = get_model(config.name_critic, config)
    model_generator = get_model(config.name_generator, config)

    # Get datasets
    if DEBUG:
        num_batches = 5
    else:
        num_batches = None

    ds_train = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split=tfds.Split.TRAIN, batch_size=config.batch_size, 
        num_batches=num_batches)

    ds_train_for_eval = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split=tfds.Split.TRAIN, batch_size=config.batch_size,
        num_batches=10)
    
    ds_val = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split=tfds.Split.VALIDATION, batch_size=config.batch_size, 
        num_batches=num_batches)

    ds_val_out = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split="validation_out", batch_size=config.batch_size,
        num_batches=num_batches)

    ds_test = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split=tfds.Split.TEST, batch_size=config.batch_size,
        num_batches=num_batches)

    ds_test_in = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split="test_in", batch_size=config.batch_size,
        num_batches=num_batches)

    ds_test_out = _get_dataset(config.dataset, model_classifier, config.test_domain,
        split="test_out", batch_size=config.batch_size,
        num_batches=num_batches)

    # TODO: add test set - done
    
    show_inputs = iter(ds_train)
    _ = model_classifier(next(show_inputs)["image"])

    # Set up checkpointing
    if args.reload_ckpt != "None":
        ckpt = tf.train.Checkpoint(model=model_classifier, global_step=global_step)
        manager = tf.train.CheckpointManager(ckpt, checkpoint_dir, max_to_keep=3)
        status = ckpt.restore(manager.latest_checkpoint)
        status.assert_consumed()
    else:
        ckpt = tf.train.Checkpoint(model=model_classifier, global_step=global_step)
        manager = tf.train.CheckpointManager(ckpt, model_dir, max_to_keep=3) 

    writer = tf.summary.create_file_writer(manager._directory)
    with writer.as_default(), tf.summary.record_if(lambda: int(global_step.numpy()) % 100 == 0):
        for epoch in range(epoch_start, config.num_epochs):
            
            start_time = time.time()

            train_one_epoch(model_classifier=model_classifier, train_input=ds_train, 
                optimizer=optimizer, global_step=global_step, config=config)

            train_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_train_for_eval,
                summary_directory=os.path.join(manager._directory, "train"), 
                global_step=global_step, config=config, training=False)
            
            val_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_val,
                summary_directory=os.path.join(manager._directory, "val_rand"), 
                global_step=global_step, config=config, training=False)

            test_out_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_test_out,
                summary_directory=os.path.join(manager._directory, "test_out"),
                global_step=global_step, config=config, training=False)

            test_in_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_test_in,
                summary_directory=os.path.join(manager._directory, "test_in"),
                global_step=global_step, config=config, training=False)
           
            if epoch == (config.num_epochs - 1):
                # full training set
                train_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_train,
                    summary_directory=os.path.join(manager._directory, "train"), 
                    global_step=global_step, config=config, training=False)
                # full test_out set
                test_out_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_test_out,
                    summary_directory=os.path.join(manager._directory, "test_out"),
                    global_step=global_step, config=config, training=False)
                # full test_in set
                test_in_metr = eval_one_epoch(model_classifier=model_classifier, dataset=ds_test_in,
                    summary_directory=os.path.join(manager._directory, "ds_test_in"),
                    global_step=global_step, config=config, training=False)


            manager.save()

            logging.info("\n #### \n epoch: %d, time: %0.2f" % 
                (epoch, time.time() - start_time))
            logging.info("Global step: {}".format(global_step.numpy()))
            logging.info("train_accuracy: {:2f}, train_loss: {:4f}".format(
                train_metr['accuracy'], train_metr['loss']))
            logging.info("test_out_accuracy: {:2f}, test_out_loss: {:4f}".format(
                test_out_metr['accuracy'], test_out_metr['loss']))
            logging.info("test_in_accuracy: {:2f}, test_in_loss: {:4f}".format(
                test_in_metr['accuracy'], test_in_metr['loss']))
           

            if epoch == epoch_start:
                dir_path = os.path.dirname(os.path.realpath(__file__))
                copy_source(dir_path, manager._directory)

    
    # Mark experiment as completed
    # TODO: add other metrics
    exp_repo.mark_experiment_as_completed(exp_id, 
        train_accuracy=train_metr['accuracy'],
        test_out_accuracy=test_out_metr['accuracy'],
        test_in_accuracy=test_in_metr['accuracy'])

if __name__ == "__main__":
    main()