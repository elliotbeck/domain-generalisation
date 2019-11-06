import tensorflow as tf
import util
import json

with open('DAOT/configs/config_class_daot.json', 'r') as myfile:
    data=myfile.read()
config_dic2 = json.loads(data)


class ResNet50(tf.keras.Model):
    INPUT_SHAPE = [224, 224]

    def __init__(self, num_classes, resnet_weights, config, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config

        in_shape = self.input_shape + [3]

        self.model = tf.keras.Sequential([
            tf.compat.v1.keras.applications.ResNet50(include_top=False,
                                                        weights=resnet_weights, input_shape=in_shape),
            tf.keras.layers.Flatten(),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(1028, activation='relu'),
            tf.keras.layers.Dropout(0.5),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(1028, activation='relu'),
            tf.keras.layers.Dropout(0.5),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(34, activation='relu'),
            tf.keras.layers.Dropout(0.5),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(num_classes, activation='softmax')
        ])
        self.model.build([None] + self.input_shape + [3])  # Batch input shape.

    def call(self, inputs, training=None, mask=None):
        return self.model(inputs, training, mask)

    @property
    def input_shape(self):
        return ResNet50.INPUT_SHAPE


class generator(tf.keras.Model):
    INPUT_SHAPE = [224, 224]

    def __init__(self, config, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config

        in_shape = self.input_shape + [3]

        self.model = tf.keras.Sequential([
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Conv2D(kernel_size=(3), filters=3 ,strides=(1), input_shape=in_shape, padding="same",
                                    kernel_initializer=tf.keras.initializers.GlorotNormal(), activation='relu'),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Conv2D(kernel_size=(1), filters=3,strides=(1), padding="same", 
                                    kernel_initializer=tf.keras.initializers.GlorotNormal(), activation='tanh')
        ])
        self.model.build([None] + self.input_shape + [3])  # Batch input shape.

    def call(self, inputs, training=None, mask=None):
        X_shortcut = inputs
        output = tf.keras.layers.add([config_dic2["lambd"]*self.model(inputs, training, mask), X_shortcut])
        #output = tf.keras.activations.tanh(output)
        return output
    
        #return tf.math.add(self.model(inputs, training, mask), X_shortcut) # have to replace 1 with lambda from config

    @property
    def input_shape(self):
        return generator.INPUT_SHAPE

class critic(tf.keras.Model):
    INPUT_SHAPE = [224, 224]

    def __init__(self, num_classes, resnet_weights, config, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config

        in_shape = self.input_shape + [3]

        self.model = tf.keras.Sequential([
            tf.compat.v1.keras.applications.ResNet50(include_top=False,
                                                        weights=resnet_weights, input_shape=in_shape),
            tf.keras.layers.Flatten(),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(1028, activation='relu'),
            tf.keras.layers.Dropout(0.5),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(1028, activation='relu'),
            tf.keras.layers.Dropout(0.5),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dense(34, activation='relu')
            #tf.keras.layers.Dropout(0.5),
            #tf.keras.layers.BatchNormalization(),
            #tf.keras.layers.Dense(34, activation='relu')
            #tf.keras.layers.Dense(num_classes, activation='softmax')
        ])
        self.model.build([None] + self.input_shape + [3])  # Batch input shape.

    def call(self, inputs, training=None, mask=None):
        return self.model(inputs, training, mask)

    @property
    def input_shape(self):
        return critic.INPUT_SHAPE