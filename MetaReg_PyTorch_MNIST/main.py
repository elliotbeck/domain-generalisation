import torch
import random
from util import preprocess
from torch import nn, optim
import torchvision.datasets as datasets
import argparse
import numpy as np
from models import model_feature, model_task, model_regularizer
from torch.utils import data
from data_loader import mnist
from train import validate_epoch, train_one_epoch_metatrain, train_one_epoch_full

parser = argparse.ArgumentParser(description='PACS')
parser.add_argument('--hidden_dim', type=int, default=256)
parser.add_argument('--batch_size', type=int, default=64)
parser.add_argument('--lr', type=float, default=0.001)
parser.add_argument('--epochs_metatrain', type=int, default=10)
parser.add_argument('--epochs_full', type=int, default=15)
parser.add_argument('--num_classes', type=int, default=1)
parser.add_argument('--meta_train_steps', type=int, default=20)
parser.add_argument('--seed', type=int, default=1)
flags = parser.parse_args()

#print setup
print('Flags:')
for k,v in sorted(vars(flags).items()):
  print("\t{}: {}".format(k, v))

# set seed
random.seed(flags.seed)

# load data
mnist_train = datasets.MNIST(root='./data', train=True, download=True, transform=None)
# 30000 obs. per train set
train_data1 = (mnist_train.train_data[::2], mnist_train.train_labels[::2])
train_data2 = (mnist_train.train_data[1::2], mnist_train.train_labels[1::2]) 
# 10000 obs. in test set
mnist_test = datasets.MNIST(root='./data', train=False, download=True, transform=None)
test_data = (mnist_test.test_data, mnist_test.test_labels)

# put data in dataloader
train_data1 = mnist(train_data1, 0.2)
train_data2 = mnist(train_data2, 0.1)
test_data = mnist(test_data, 0.9)
train_data_full = data.DataLoader(data.ConcatDataset([train_data1, train_data2]), num_workers=1, batch_size=flags.batch_size, 
                              shuffle=True, drop_last=True)
train_data1 = data.DataLoader(train_data1, num_workers=1, batch_size=flags.batch_size, 
                              shuffle=True, drop_last=True)
train_data2 = data.DataLoader(train_data2, num_workers=1, batch_size=flags.batch_size, 
                              shuffle=True, drop_last=True)                                
test_data = data.DataLoader(test_data, num_workers=1, batch_size=flags.batch_size, 
                              shuffle=True, drop_last=True)            

# load models
model_feature_final = model_feature(flags.hidden_dim).cuda()
model_feature = model_feature(flags.hidden_dim).cuda()
model_task1 = model_task(model_feature, flags.hidden_dim, flags.num_classes).cuda()
model_task2 = model_task(model_feature, flags.hidden_dim, flags.num_classes).cuda()
model_regularizer = model_regularizer(flags.hidden_dim, flags.num_classes).cuda()
model_final = model_task(model_feature_final, flags.hidden_dim, flags.num_classes).cuda()

# set train function 
def trainer(model_task1, model_task2, model_regularizer, model_final, train_data_full,
            train_data1, train_data2, test_data, epochs_metatrain, epochs_full, learning_rate):
    # set loss function for all NNs
    loss_function = nn.BCEWithLogitsLoss()

    # set optimizers for metatraining
    optimizer_task1 = optim.SGD(model_task1.parameters(), lr=learning_rate, momentum=0.9)
    optimizer_task2 = optim.SGD(model_task2.parameters(), lr=learning_rate, momentum=0.9)
    
    # metatraining
    for epoch in range(epochs_metatrain):  

        train_one_epoch_metatrain(model_task1, model_task2, model_regularizer, train_data1, 
                        train_data2,  optimizer_task1, optimizer_task2,  
                        loss_function, learning_rate, flags.meta_train_steps)
        #print status         
        template = 'Step {} of {} of Meta Learning completed'
        print(template.format(epoch+1, epochs_metatrain)) 

    # set optimizer for metalearning
    optimizer_final = optim.SGD(model_final.parameters(), lr=learning_rate, momentum=0.9)

    # full training 
    print('Start Training of Full Model')
    for epoch in range(epochs_full):
        train_one_epoch_full(train_data_full, model_final, model_regularizer, loss_function, optimizer_final)
        # validate epoch on validation set
        loss_train, accuracy_train, loss_test, accuracy_test = validate_epoch(train_data_full, test_data, model_final, loss_function)

        # print the metrics
        template = 'Epoch {}, Loss: {}, Accuracy: {}, Test Loss: {}, Test Accuracy: {}'
        print(template.format(epoch,
                                np.array2string(loss_train, precision=2, floatmode='fixed'),
                                np.array2string(accuracy_train*100, precision=2, floatmode='fixed'),
                                np.array2string(loss_test, precision=2, floatmode='fixed'),
                                np.array2string(accuracy_test*100, precision=2, floatmode='fixed')))          
                
    print('Finished Training')

if __name__ == "__main__":
  trainer(model_task1, model_task2, model_regularizer, model_final, train_data_full, 
          train_data1, train_data2, test_data, flags.epochs_metatrain, 
          flags.epochs_full, flags.lr)






