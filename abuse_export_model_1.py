#! /usr/bin/env python

import tensorflow as tf
import numpy as np
import os
import time
import datetime
import data_helpers
from text_cnn import TextCNN
from tensorflow.contrib import learn
import nltk

from tensorflow.contrib.session_bundle import exporter

# Parameters
# ==================================================

# Data loading params
#tf.flags.DEFINE_float("dev_sample_percentage", .1, "Percentage of the training data to use for validation")
tf.flags.DEFINE_string("train_file", "../../data/2017-06-08/dataReplicated.csv", "Data source for the train data.")
tf.flags.DEFINE_string("test_file", "../../data/2017-06-07/dataReplicated.csv", "Data source for the test data.")

# Model Hyperparameters
tf.flags.DEFINE_integer("embedding_dim", 128, "Dimensionality of character embedding (default: 128)")
tf.flags.DEFINE_string("filter_sizes", "3,4,5", "Comma-separated filter sizes (default: '3,4,5')")
tf.flags.DEFINE_integer("num_filters", 128, "Number of filters per filter size (default: 128)")
tf.flags.DEFINE_float("dropout_keep_prob", 0.5, "Dropout keep probability (default: 0.5)")
tf.flags.DEFINE_float("l2_reg_lambda", 0.0, "L2 regularization lambda (default: 0.0)")

# Training parameters
tf.flags.DEFINE_integer("batch_size", 64, "Batch Size (default: 64)")
tf.flags.DEFINE_integer("num_epochs", 200, "Number of training epochs (default: 200)")
tf.flags.DEFINE_integer("evaluate_every", 100, "Evaluate model on dev set after this many steps (default: 100)")
tf.flags.DEFINE_integer("checkpoint_every", 100, "Save model after this many steps (default: 100)")
tf.flags.DEFINE_integer("num_checkpoints", 5, "Number of checkpoints to store (default: 5)")
# Misc Parameters
tf.flags.DEFINE_boolean("allow_soft_placement", True, "Allow device soft device placement")
tf.flags.DEFINE_boolean("log_device_placement", False, "Log placement of ops on devices")

FLAGS = tf.flags.FLAGS
FLAGS._parse_flags()
print("\nParameters:")
for attr, value in sorted(FLAGS.__flags.items()):
    print("{}={}".format(attr.upper(), value))
print("")

def myTokenize (iter):
    for value in iter:
        yield nltk.word_tokenize(value)

# Data Preparation
# ==================================================

# Load data
print("Loading data...")
x_text, y = data_helpers.loadData(FLAGS.train_file)

# Build vocabulary
#max_document_length = min([len(x.split(" ")) for x in x_text])
max_document_length = 200
print("max_document_length ", max_document_length)
vocab_processor = learn.preprocessing.VocabularyProcessor(max_document_length, tokenizer_fn = myTokenize)
x = np.array(list(vocab_processor.fit_transform(x_text)))

# Randomly shuffle data
np.random.seed(10)
shuffle_indices = np.random.permutation(np.arange(len(y)))
x_shuffled = x[shuffle_indices]
y_shuffled = y[shuffle_indices]

# Split train/test set
# TODO: This is very crude, should use cross-validation
dev_sample_index = 0
x_train = x_shuffled
y_train = y_shuffled
print("Vocabulary Size: {:d}".format(len(vocab_processor.vocabulary_)))
print("Train split: {:d}".format(len(y_train)))


# Training
# ==================================================

with tf.Graph().as_default():
    session_conf = tf.ConfigProto(
        allow_soft_placement=FLAGS.allow_soft_placement,
        log_device_placement=FLAGS.log_device_placement)
    sess = tf.Session(config=session_conf)
    with sess.as_default():
        cnn = TextCNN(
            sequence_length=x_train.shape[1],
            num_classes=y_train.shape[1],
            vocab_size=len(vocab_processor.vocabulary_),
            embedding_size=FLAGS.embedding_dim,
            filter_sizes=list(map(int, FLAGS.filter_sizes.split(","))),
            num_filters=FLAGS.num_filters,
            l2_reg_lambda=FLAGS.l2_reg_lambda)

        # Define Training procedure
        global_step = tf.Variable(0, name="global_step", trainable=False)
        optimizer = tf.train.AdamOptimizer(1e-3)
        grads_and_vars = optimizer.compute_gradients(cnn.loss)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        # Keep track of gradient values and sparsity (optional)
        grad_summaries = []
        for g, v in grads_and_vars:
            if g is not None:
                grad_hist_summary = tf.summary.histogram("{}/grad/hist".format(v.name), g)
                sparsity_summary = tf.summary.scalar("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
                grad_summaries.append(grad_hist_summary)
                grad_summaries.append(sparsity_summary)
        grad_summaries_merged = tf.summary.merge(grad_summaries)

        # Output directory for models and summaries
        timestamp = str(int(time.time()))
        out_dir = os.path.abspath(os.path.join(os.path.curdir, "runs", timestamp))
        print("Writing to {}\n".format(out_dir))

        # Summaries for loss and accuracy
        loss_summary = tf.summary.scalar("loss", cnn.loss)
        acc_summary = tf.summary.scalar("accuracy", cnn.accuracy)

        # Train Summaries
        train_summary_op = tf.summary.merge([loss_summary, acc_summary, grad_summaries_merged])
        train_summary_dir = os.path.join(out_dir, "summaries", "train")
        train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

        # Dev summaries
        dev_summary_op = tf.summary.merge([loss_summary, acc_summary])
        dev_summary_dir = os.path.join(out_dir, "summaries", "dev")
        dev_summary_writer = tf.summary.FileWriter(dev_summary_dir, sess.graph)

        # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
        checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
        checkpoint_prefix = os.path.join(checkpoint_dir, "model")
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        saver = tf.train.Saver(tf.global_variables(), max_to_keep=FLAGS.num_checkpoints)
        # Write vocabulary
        vocab_processor.save(os.path.join(out_dir, "vocab"))

        # Initialize all variables
        sess.run(tf.global_variables_initializer())

        def train_step(x_batch, y_batch):
            """
            A single training step
            """
            feed_dict = {
                cnn.input_x: x_batch,
                cnn.input_y: y_batch
            }
            _, step, summaries, loss, accuracy = sess.run(
                [train_op, global_step, train_summary_op, cnn.loss, cnn.accuracy],
                feed_dict)
            time_str = datetime.datetime.now().isoformat()
            print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
            train_summary_writer.add_summary(summaries, step)

        # Generate batches
        batches = data_helpers.batch_iter(
            list(zip(x_train, y_train)), FLAGS.batch_size, FLAGS.num_epochs)
        # Training loop. For each batch...
        for batch in batches:
            x_batch, y_batch = zip(*batch)
            train_step(x_batch, y_batch)
            current_step = tf.train.global_step(sess, global_step)
            if current_step % FLAGS.checkpoint_every == 0:
                path = saver.save(sess, checkpoint_prefix, global_step=current_step)
                print("Saved model checkpoint to {}\n".format(path))
            if current_step // FLAGS.checkpoint_every == 30 and current_step % FLAGS.checkpoint_every == 0:
                print("Trying to export new model...")
                export_path = "/home/zi/sandbox/honest-ranger/data/save/serving/model_gpu/" + str(1)

                builder = tf.saved_model.builder.SavedModelBuilder(export_path)

                # Building classification signature
                print("Zi: Created signature Classes ...")
                print(type(cnn.input_x), cnn.input_x.shape)
                serving_input_x = tf.saved_model.utils.build_tensor_info(
                    cnn.input_x)
                y = np.argmax(y_batch, axis=1)
                values, indices  = tf.nn.top_k(y, 10)
                classification_outputs_classes = tf.saved_model.utils.build_tensor_info( values)
                classification_signature = (
                    tf.saved_model.signature_def_utils.build_signature_def(
                        inputs={
                            tf.saved_model.signature_constants.CLASSIFY_INPUTS:
                                serving_input_x
                        },
                        outputs={
                            tf.saved_model.signature_constants.CLASSIFY_OUTPUT_CLASSES:
                                classification_outputs_classes,
                            tf.saved_model.signature_constants.CLASSIFY_OUTPUT_SCORES:
                                classification_outputs_classes
                        },
                        method_name=tf.saved_model.signature_constants.
                            CLASSIFY_METHOD_NAME))

                tensor_info_x = tf.saved_model.utils.build_tensor_info(cnn.input_x)
                tensor_info_y = tf.saved_model.utils.build_tensor_info(cnn.predictions)

                prediction_signature = (
                    tf.saved_model.signature_def_utils.build_signature_def(
                        inputs={'text': tensor_info_x},
                        outputs={'scores': tensor_info_y},
                        method_name=tf.saved_model.signature_constants.PREDICT_METHOD_NAME))

                # need to initialize ops only once, not in a loop
                legacy_init_op = tf.group(tf.tables_initializer(), name='legacy_init_op')


                builder.add_meta_graph_and_variables(
                    sess, [tf.saved_model.tag_constants.SERVING],
                    signature_def_map={
                        'predict_text':
                            prediction_signature,
                        tf.saved_model.signature_constants.DEFAULT_SERVING_SIGNATURE_DEF_KEY:
                            classification_signature,
                    },
                    legacy_init_op=legacy_init_op)

                builder.save()