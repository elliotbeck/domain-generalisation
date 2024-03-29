#!/bin/sh

# Hard coded settings for resources
# time limit
export ttime=30:00
# number of gpus per job
export num_gpu_per_job=1
# memory per job
export mem_per_gpu=30000

export JOB_NAME='crossgrad'

# load python
module load eth_proxy python_gpu/3.6.4
module load cuda/10.0.130
module load cudnn/7.6.4


export CONFIG="/cluster/home/ebeck/DomainGeneralisation/CrossGrad/configs/config_class_crossgrad.json"

export DECAY_EVERY=5000
export NUM_EPOCHS=40
export BATCH_SIZE=18

for VAR_LEARN_RATE in 0.001 0.0001 0.00001
do
    export LEARN_RATE=$VAR_LEARN_RATE
    for  VAR_DO_RATE in 0.025 0.075 0.1 
    do
        export DO_RATE=$VAR_DO_RATE
        for  VAR_L2_PEN in 0.0001 0.00001 0.000001 0
        do
            export L2_PEN=$VAR_L2_PEN 
            sh submit-train-2.0.sh
        done
    done    
done