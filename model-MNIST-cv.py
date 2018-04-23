from __future__ import print_function, division

from keras.datasets import mnist
from keras.layers import Input, Dense, Reshape, Flatten, Dropout
from keras.layers import BatchNormalization, Activation, ZeroPadding2D
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.convolutional import UpSampling2D, Conv2D
from keras.models import Sequential, Model
from keras.optimizers import Adam
from keras.utils import to_categorical

from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

import matplotlib.pyplot as plt
import numpy as np
import time

# Fixing random state for reproducibility
seed = 19680801
np.random.seed(seed)

class DCGAN():
    def __init__(self):
        # MNIST input shape is 28x28x1
        self.img_rows = 28
        self.img_cols = 28
        self.channels = 1
        self.num_classes = 10
        self.training_history = {
                'D_loss': [], 
                'D_acc': [],
                'G_loss': [], 
                'G_acc': [],
                }
        
        # While previous GAN work has used momentum to accelerate training, we used the Adam optimizer
        # (Kingma & Ba, 2014) with tuned hyperparameters. We found the suggested learning rate of 0.001,
        # to be too high, using 0.0002 instead. Additionally, we found leaving the momentum term β1 at the
        # suggested value of 0.9 resulted in training oscillation and instability while reducing it to 0.5 helped 
        # stabilize training
        optimizer = Adam(0.0002, 0.5)

        # Build discriminator's model
        self.discriminator = self.build_discriminator()
        # Compile discriminator's model, i.e. define its learning process
        # binary crossentropy is used to distinguish among real or fake samples
        # categorical entropy is to distinguish among which real category is (nuclei or non-nuclei)
        self.discriminator.compile(loss=['binary_crossentropy', 'categorical_crossentropy'], 
            loss_weights=[0.5, 0.5],
            optimizer=optimizer,
            metrics=['accuracy'])

        # Build and compile the generator
        self.generator = self.build_generator()
        self.generator.compile(loss='binary_crossentropy', optimizer=optimizer)

        # The generator takes noise as input and generates imgs
        z = Input(shape=(100,))
        img = self.generator(z)

        # For the combined model we will only train the generator
        self.discriminator.trainable = False

        # The discriminator takes generated images as input and determines validity
        valid, _ = self.discriminator(img)

        # The combined model  (stacked generator and discriminator) takes
        # noise as input => generates images => determines validity 
        self.combined = Model(z, valid)
        self.combined.compile(loss='binary_crossentropy', optimizer=optimizer)

    def build_generator(self):
        # This model replaced any pooling layers with strided convolutions
        # Allowing it to learn its own spatial upsampling
        
        model = Sequential()
        
        model.add(Dense(128 * 7 * 7, activation="relu", input_dim=100))
        model.add(Reshape((7, 7, 128)))
        model.add(BatchNormalization(momentum=0.8))
        # fractionally-strided convolution, do not confuse with deconvolution operation
        model.add(UpSampling2D())
        model.add(Conv2D(128, kernel_size=3, padding="same"))
        # using a bounded activation allowed the model to learn more quickly to saturate and cover the color space of the training distribution
        model.add(Activation("relu"))
        model.add(BatchNormalization(momentum=0.8))
        #upsampling is the opposite to pooling. Repeats the rows and columns of the data
        model.add(UpSampling2D())
        model.add(Conv2D(64, kernel_size=3, padding="same"))
        model.add(Activation("relu"))
        model.add(BatchNormalization(momentum=0.8))
        #flatten to the amount of channels
        model.add(Conv2D(self.channels, kernel_size=3, padding="same"))
        model.add(Activation("tanh"))

        #model.summary()

        noise = Input(shape=(100,))
        img = model(noise)

        return Model(noise, img)

    def build_discriminator(self):
        # This model replaced any pooling layers with strided convolutions
        # Allowing it to learn its own spatial downsampling
        img_shape = (self.img_rows, self.img_cols, self.channels)
        
        # A Sequential model is a linear stack of layers.
        model = Sequential()
        
        # Create a Sequential model by simply adding layers via the .add() method
        # 32 filters, 3x3 kernel size, stride 2, input_shape is 28x28x1, same: pad so the output and input size are equal 
        model.add(Conv2D(32, kernel_size=3, strides=2, input_shape=img_shape, padding="same"))
        # f(x) = alpha * x for x < 0, f(x) = x for x >= 0.
        # Leaky rectified activation worked well, especially for higher resolution modeling.
        # This is in contrast to the original GAN paper, which used the maxout activation
        model.add(LeakyReLU(alpha=0.2))
        # drops 25% of the input units
        model.add(Dropout(0.25))

        model.add(Conv2D(64, kernel_size=3, strides=2, padding="same"))
        #A zero-padding layer. Adds rows and columns of zeros to the image
        model.add(ZeroPadding2D(padding=((0,1),(0,1))))
        model.add(LeakyReLU(alpha=0.2))
        model.add(Dropout(0.25))
        # Normalize the activations of the previous layer at each batch to reduce its covariance shift,
        # i.e., the amount that the distribution of each layer shift around.

        # This helps deal with training problems that arise due to poor initialization and helps gradient flow in deeper models. 
        # This proved critical to get deep generators to begin learning, preventing the generator from collapsing all samples
        # to a single point which is a common failure mode observed in GANs. 
        # 
        # Directly applying batchnorm to all layers, however, resulted in sample oscillation and model instability. 
        # This was avoided by not applying batchnorm to the generator output layer and the discriminator input layer
        model.add(BatchNormalization(momentum=0.8))
        model.add(Conv2D(128, kernel_size=3, strides=2, padding="same"))
        model.add(LeakyReLU(alpha=0.2))
        model.add(Dropout(0.25))
        model.add(BatchNormalization(momentum=0.8))
        model.add(Conv2D(256, kernel_size=3, strides=1, padding="same"))
        model.add(LeakyReLU(alpha=0.2))
        model.add(Dropout(0.25))
        
        model.add(Flatten())
        
        #model.summary()
        
        # instantiate a Keras tensor
        img = Input(shape=img_shape)
        features = model(img)
        
        # valid indicates if the image is real or fake
        valid = Dense(1, activation="sigmoid")(features)
        # iff the image is real, label indicates which type of image it is
        label = Dense(self.num_classes+1, activation="softmax")(features)
        
        # Given an img (x)  and a label(y), instantiate a Model.
        # Once instantiated, this model will include all layers required in the computation of y given x.
        return Model(img, [valid, label])

    def train(self, X_train, y_train, epochs=200, batch_size=32, save_interval=60):
        
        half_batch = int(batch_size / 2)
        
        # Class weights:
        # To balance the difference in occurences of digit class labels. 
        # 50% of labels that the discriminator trains on are 'fake'.
        # Weight = 1 / frequency
        cw1 = {0: 1, 1: 1}
        cw2 = {i: self.num_classes / half_batch for i in range(self.num_classes)}
        cw2[self.num_classes] = 1 / half_batch
               
        for epoch in range(epochs):
            # ---------------------
            #  Train Discriminator
            # ---------------------
    
            # Select a random half batch of images
            idx = np.random.randint(0, X_train.shape[0], half_batch)
            imgs = X_train[idx]
    
            # Draw random samples from a Gaussian distribution.
            noise = np.random.normal(0, 1, (half_batch, 100))
            # Generate a half batch of new images
            gen_imgs = self.generator.predict(noise)
            
            valid = np.ones((half_batch, 1))
            fake = np.zeros((half_batch, 1))
            
            # Convert labels to categorical one-hot encoding
            labels = to_categorical(y_train[idx], num_classes=self.num_classes+1)
            fake_labels = to_categorical(np.full((half_batch, 1), self.num_classes), num_classes=self.num_classes+1)
    
            # Train the discriminator (real classified as ones and fakes as zeros)
            # train_on_batch: Single gradient update over one batch of samples
            d_loss_real = self.discriminator.train_on_batch(imgs, [valid, labels], class_weight=[cw1, cw2])
            d_loss_fake = self.discriminator.train_on_batch(gen_imgs, [fake, fake_labels], class_weight=[cw1, cw2])
            d_loss = 0.5 * np.add(d_loss_real, d_loss_fake)
    
            # ---------------------
            #  Train Generator
            # ---------------------
    
            noise = np.random.normal(0, 1, (batch_size, 100))
            validity = np.ones((batch_size, 1))
            
            # Train the generator (wants discriminator to mistake images as real)
            g_loss = self.combined.train_on_batch(noise, validity, class_weight=[cw1, cw2])
    
            self.training_history["D_loss"].append(d_loss[0]);
            self.training_history["D_acc"].append(100*d_loss[3]);
            self.training_history["G_loss"].append(g_loss);
            self.training_history["G_acc"].append(100*d_loss[4]);
    
            # If at save interval => save generated image samples
            if epoch % save_interval == 0:
                # Plot the progress
                print ("%d: Training D [loss: %.4f, acc: %.2f%% ] - G [loss: %.4f, acc: %.2f%%]" % (epoch, d_loss[0], 100*d_loss[3], g_loss, 100*d_loss[4]))
                self.save_imgs(epoch)        
           
    def evaluate_discriminator(self, X_test, y_test):

        valid = np.ones((y_test.shape[0], 1))
        
        # Convert labels to categorical one-hot encoding
        labels = to_categorical(y_test, num_classes=self.num_classes+1)

        #  Evaluating the trained Discriminator
        scores = self.discriminator.evaluate(X_test, [valid, labels])
        
        print("\nValidating D [loss:  %.4f, acc: %.2f%%]" % (scores[0], scores[3]*100))
        
        return (scores[0], scores[3]*100)
        
    def save_imgs(self, epoch):
        r, c = 5, 5
        noise = np.random.normal(0, 1, (r * c, 100))
        gen_imgs = self.generator.predict(noise)

        # Rescale images 0 - 1
        gen_imgs = 0.5 * gen_imgs + 1

        fig, axs = plt.subplots(r, c)
        cnt = 0
        for i in range(r):
            for j in range(c):
                axs[i,j].imshow(gen_imgs[cnt, :,:,0], cmap='gray')
                axs[i,j].axis('off')
                cnt += 1
        fig.savefig("./generators_output/mnist_%d.png" % epoch)
        plt.close()
        
    def save_model(self):

        def save(model, model_name):
            model_path = "./saved_models/%s.json" % model_name
            weights_path = "./saved_models/%s_weights.hdf5" % model_name
            options = {"file_arch": model_path, 
                        "file_weight": weights_path}
            json_string = model.to_json()
            open(options['file_arch'], 'w').write(json_string)
            model.save_weights(options['file_weight'])

        save(self.generator, "mnist_gan_generator")
        save(self.discriminator, "mnist_gan_discriminator")
        save(self.combined, "mnist_gan_adversarial")
        
    def plot_training_history(self):    
        fig, axs = plt.subplots(1,2,figsize=(15,5))
        plt.title('Training History')
        # summarize history for G and D accuracy
        axs[0].plot(range(1,len(self.training_history['D_acc'])+1),self.training_history['D_acc'])
        axs[0].plot(range(1,len(self.training_history['G_acc'])+1),self.training_history['G_acc'])
        axs[0].set_title('D and G Accuracy')
        axs[0].set_ylabel('Accuracy')
        axs[0].set_xlabel('Epoch')
        axs[0].set_xticks(np.arange(1,len(self.training_history['D_acc'])+1),len(self.training_history['D_acc'])/10)
        axs[0].legend(['Discriminator', 'Generator'], loc='best')
        
        # summarize history for G and D loss
        axs[1].plot(range(1,len(self.training_history['D_loss'])+1),self.training_history['D_loss'])
        axs[1].plot(range(1,len(self.training_history['G_loss'])+1),self.training_history['G_loss'])
        axs[1].set_title('D and G Loss')
        axs[1].set_ylabel('Loss')
        axs[1].set_xlabel('Epoch')
        axs[1].set_xticks(np.arange(1,len(self.training_history['G_loss'])+1),len(self.training_history['G_loss'])/10)
        axs[1].legend(['Discriminator', 'Generator'], loc='best')
        plt.show()
        
    def predict(self, X_test, y_test):
        # Generating a predictions from the discriminator over the testing dataset
        y_pred = dcgan.discriminator.predict(X_test)
        
        # Formating predictions to remove the one_hot_encoding format
        y_pred = np.argmax(y_pred[1][:,:-1], axis=1)
        
        print ('Overall accuracy: ' + '{:2f}'.format(accuracy_score(y_test, y_pred)))
        
        # Calculating and ploting a Classification Report
        target_names = ['class 0', 'class 1', 'class 2', 'class 3', 'class 4', 'class 5', 'class 6', 'class 7', 'class 8', 'class 9']
        print("Classification report:\n %s\n" 
              % (classification_report(y_test, y_pred, target_names=target_names)))
        
        # Calculating and ploting Confusion Matrix
        cm = confusion_matrix(y_test, y_pred)
        print("Confusion matrix:\n%s" % cm)

        plt.matshow(cm)
        plt.title('Confusion matrix\n')
        plt.colorbar()
        plt.ylabel('True label')
        plt.xlabel('Predicted label')
        plt.xticks(np.arange(min(y_test), max(y_test)+1, 1.0))
        plt.yticks(np.arange(min(y_test), max(y_test)+1, 1.0))
        plt.show() 
        
def load_data_kfold(k):
    # Load the dataset
    (X_train, y_train) , (X_test, y_test) = mnist.load_data()
    
    # Normalize values from -1 to 1
    X_train = (X_train.astype(np.float32) - 127.5) / 127.5
    X_train = np.expand_dims(X_train, axis=3)
    y_train = y_train.reshape(-1, 1)
    
    X_test = (X_test.astype(np.float32) - 127.5) / 127.5
    X_test = np.expand_dims(X_test, axis=3)
    y_test = y_test.reshape(-1, 1)
    
    # define 10-fold cross validation
    folds = list(StratifiedKFold(n_splits=k, shuffle=True, random_state=seed).split(X_train, y_train))
    
    return folds, X_train, y_train, X_test, y_test
    
if __name__ == '__main__':
    folds, X_train, y_train, X_test, y_test = load_data_kfold(10)
    kfold_scores = []
    
    # Instanciate a compiled model

    start = time.time()
    # Train the model on each fold
    for fold_number, (train_idx, val_idx) in enumerate(folds):
        print('\n-----  Fold: %d  -----' % (fold_number))
                
        # Spliting training dataset into train and validation sets
        X_train_cv = X_train[train_idx]
        y_train_cv = y_train[train_idx]
        X_valid_cv = X_train[val_idx]
        y_valid_cv = y_train[val_idx]
        
        dcgan = DCGAN()
        # Fit/Train the model
        dcgan.train(X_train_cv, y_train_cv, epochs=100, batch_size=32, save_interval=10)
        
        # Evaluate the model
        _ , acc = dcgan.evaluate_discriminator(X_valid_cv, y_valid_cv)
        
        kfold_scores.append(acc)
        
    end = time.time()
    print ("\Training time: %0.1fs\n" % (end - start))
    
    #print mean of k-fold
    print("Training avg acc: %.2f%% (+/- %.2f%%)\n" % (np.mean(kfold_scores), np.std(kfold_scores)))
    
    # plot training graph        
    dcgan.plot_training_history()
    
    #evaluate the trained D model w.r.t unseen data (i.e. testing set)
    dcgan.predict(X_test, y_test)
    
    #saved the trained model
    dcgan.save_model()