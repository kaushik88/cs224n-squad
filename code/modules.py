# Copyright 2018 Stanford University
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file contains some basic model components"""

import tensorflow as tf
from tensorflow.python.ops.rnn_cell import DropoutWrapper
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import rnn_cell


class BahdanauAttn(object):
    """Module for calculating the self attention.

    In this model, we assume the keys are the context and they attend to themselves.
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size, bahdanau_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_suze: size of the value vectors. int
          bahdanau_size: Size of Bahdanau vectors. int (d3 in the slides)
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size
        self.bahdanau_size = bahdanau_size

    def build_graph(self, values, values_mask, keys):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:          
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          values: Tensor shape (batch_size, num_values, value_vec_size)  
          keys: Tensor shape (batch_size, num_keys, key_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("BahdanauAttn", reuse=tf.AUTO_REUSE):

            xi = tf.contrib.layers.xavier_initializer()
            w1 = tf.get_variable("W1", shape=(self.value_vec_size, self.bahdanau_size), initializer=xi)
            w2 = tf.get_variable("W2", shape=(self.key_vec_size, self.bahdanau_size), initializer=xi)
            v = tf.get_variable("v", shape=(self.bahdanau_size), initializer=xi)

            # (batch_size, 1, num_values, bahdanau_size)
            value_shape = values.get_shape()
            w1_values = tf.tensordot(values, w1, axes=1)
            w1_values.set_shape((value_shape[0], value_shape[1], self.bahdanau_size))
            w1_values = tf.expand_dims(w1_values, axis=1)

            # (batch_size, num_keys, 1, bahdanau_size)
            keys_shape = keys.get_shape()
            w2_keys = tf.tensordot(keys, w2, axes=1)
            w2_keys.set_shape((keys_shape[0], keys_shape[1], self.bahdanau_size))
            w2_keys = tf.expand_dims(w2_keys, axis=2)

            # (batch_size, num_keys, num_values)
            attn_logits = tf.reduce_sum(tf.multiply(tf.nn.tanh(w1_values + w2_keys), v), axis=3)

            # (batch_size, 1, num_values)
            attn_logits_mask = tf.expand_dims(values_mask, 1)

            # (batch_size, num_keys, num_values). take softmax over values
            _, attn_dist = masked_softmax(attn_logits, attn_logits_mask, 2) 

            # Use attention distribution to take weighted sum of values
            output = tf.matmul(attn_dist, values) # shape (batch_size, num_keys, key_vec_size)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_dist, output

class RNNEncoder(object):
    """
    General-purpose module to encode a sequence using a RNN.
    It feeds the input through a RNN and returns all the hidden states.

    Note: In lecture 8, we talked about how you might use a RNN as an "encoder"
    to get a single, fixed size vector representation of a sequence
    (e.g. by taking element-wise max of hidden states).
    Here, we're using the RNN as an "encoder" but we're not taking max;
    we're just returning all the hidden states. The terminology "encoder"
    still applies because we're getting a different "encoding" of each
    position in the sequence, and we'll use the encodings downstream in the model.

    This code uses a bidirectional GRU, but you could experiment with other types of RNN.
    """

    def __init__(self, hidden_size, keep_prob):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.keep_prob = keep_prob
        self.rnn_cell_fw = rnn_cell.LSTMCell(self.hidden_size)
        self.rnn_cell_fw = DropoutWrapper(self.rnn_cell_fw, input_keep_prob=self.keep_prob)
        self.rnn_cell_bw = rnn_cell.LSTMCell(self.hidden_size)
        self.rnn_cell_bw = DropoutWrapper(self.rnn_cell_bw, input_keep_prob=self.keep_prob)

    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        with vs.variable_scope("RNNEncoder"):

            # Note: The bidirectional_dynamic_rnn needs the actual size of the input which can be found be summing
            # the masks (as it has 1s for every valid input).
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # Note: fw_out and bw_out are the hidden states for every timestep.
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, inputs, input_lens, dtype=tf.float32)

            # Concatenate the forward and backward hidden states
            # shape is (batch_size, seq_len, 2*hidden_size)
            out = tf.concat([fw_out, bw_out], 2)

            # Apply dropout
            out = tf.nn.dropout(out, self.keep_prob)

            return out


class SimpleSoftmaxLayer(object):
    """
    Module to take set of hidden states, (e.g. one for each context location),
    and return probability distribution over those states.
    """

    def __init__(self):
        pass

    def build_graph(self, inputs, masks):
        """
        Applies one linear downprojection layer, then softmax.

        Inputs:
          inputs: Tensor shape (batch_size, seq_len, hidden_size)
          masks: Tensor shape (batch_size, seq_len)
            Has 1s where there is real input, 0s where there's padding.

        Outputs:
          logits: Tensor shape (batch_size, seq_len)
            logits is the result of the downprojection layer, but it has -1e30
            (i.e. very large negative number) in the padded locations
          prob_dist: Tensor shape (batch_size, seq_len)
            The result of taking softmax over logits.
            This should have 0 in the padded locations, and the rest should sum to 1.
        """
        with vs.variable_scope("SimpleSoftmaxLayer"):

            # Linear downprojection layer
            # Note - This creates a 'weight' matrix W and initializes it to xavier and multiplies it with inputs
            # and adds the bias and returns this as the logits.
            logits = tf.contrib.layers.fully_connected(inputs, num_outputs=1, activation_fn=None) # shape (batch_size, seq_len, 1)
            logits = tf.squeeze(logits, axis=[2]) # shape (batch_size, seq_len)

            # Take softmax over sequence
            masked_logits, prob_dist = masked_softmax(logits, masks, 1)

            return masked_logits, prob_dist

class BasicAttn(object):
    """Module for basic attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".

    In the baseline model, the keys are the context hidden states
    and the values are the question hidden states.

    We choose to use general terminology of keys and values in this module
    (rather than context and question) to avoid confusion if you reuse this
    module with other inputs.
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("BasicAttn"):

            # Calculate attention distribution
            # Note : This assumes that the value_vec_size == key_vec_size
            values_t = tf.transpose(values, perm=[0, 2, 1]) # (batch_size, value_vec_size, num_values)
            attn_logits = tf.matmul(keys, values_t) # shape (batch_size, num_keys, num_values)
            attn_logits_mask = tf.expand_dims(values_mask, 1) # shape (batch_size, 1, num_values)
            _, attn_dist = masked_softmax(attn_logits, attn_logits_mask, 2) # shape (batch_size, num_keys, num_values). take softmax over values

            # Use attention distribution to take weighted sum of values
            output = tf.matmul(attn_dist, values) # shape (batch_size, num_keys, value_vec_size)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_dist, output

class CoAttn(object):
    """Module for Co Attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys, keys_mask):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)
          keys_mask: Tensor shape (batch_size, num_keys).

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("CoAttn"):

            # Calculate attention distribution
            # Note : This assumes that the value_vec_size == key_vec_size
            xi = tf.contrib.layers.xavier_initializer()
            W = tf.get_variable("weights", shape=(self.value_vec_size, self.value_vec_size), dtype=tf.float32, initializer=xi)
            b = tf.get_variable("bias", shape=(1, self.value_vec_size), dtype=tf.float32, initializer=tf.constant_initializer(0.0))
            context_sentinel = tf.get_variable("context_sentinel", shape=(1, 1, self.key_vec_size), initializer=xi)
            question_sentinel = tf.get_variable("question_sentinel", shape=(1, 1, self.value_vec_size), initializer=xi)
            one = tf.constant(1, dtype=tf.int32, shape=(1,1))
            
            # shape = (batch_size, num_values, value_vec_size)
            value_shape = values.get_shape()
            value_dash = tf.tensordot(values, W, axes=1)
            value_dash.set_shape(value_shape)
            value_dash = tf.nn.tanh(value_dash + tf.expand_dims(b, axis=0))

            # shape = (batch_size, num_keys + 1, key_vec_size)
            new_context_state = tf.concat([keys, tf.tile(context_sentinel, tf.stack([tf.shape(keys)[0], 1, 1]))], axis=1)
            updated_keys_mask = tf.concat([keys_mask, tf.tile(one, tf.stack([tf.shape(keys)[0], 1]))], axis=1)

            # shape = (batch_size, num_values+1, value_vec_size)
            new_question_state = tf.concat([values, tf.tile(question_sentinel, tf.stack([tf.shape(values)[0], 1, 1]))], axis=1)
            updated_values_mask = tf.concat([values_mask, tf.tile(one, tf.stack([tf.shape(values)[0], 1]))], axis=1)

            # shape = (batch_size, num_keys + 1, num_values + 1)
            L_matrix = tf.matmul(new_context_state, tf.transpose(new_question_state, perm=[0,2,1]))
            _, c2q_attn_dist = masked_softmax(L_matrix, tf.expand_dims(updated_values_mask, axis=1), 2)
            
            # shape = (batch_size, num_keys + 1, values_vec_size)
            c2q_attn_output = tf.matmul(c2q_attn_dist, new_question_state)

            # shape = (batch_size, num_values + 1, num_keys +1)
            L_matrix_t = tf.transpose(L_matrix, perm=[0, 2, 1])
            _, q2c_attn_dist = masked_softmax(L_matrix_t, tf.expand_dims(updated_keys_mask, axis=1), 2)            

            # shape = (batch_size, num_values+1, key_vec_size)
            q2c_attn_output = tf.matmul(q2c_attn_dist, new_context_state)

            # shape = (batch_size, num_keys + 1, key_vec_size)
            co_attention = tf.matmul(c2q_attn_dist, q2c_attn_output)

            encoder = RNNEncoder(self.key_vec_size, self.keep_prob)
            output = encoder.build_graph(tf.concat([co_attention[:,:-1,:], c2q_attn_output[:,:-1,:]], axis=2), keys_mask)

            return c2q_attn_dist, output

class BidafAttn(object):
    """Module for bidaf attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".

    In the bidaf model, the keys are the context hidden states and the values are the question hidden states.
    The attention flows both ways i.e from context to question and question to context.

    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys, keys_mask):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("BidafAttn"):


            # shape : 2*Hidden_size
            wsim1 = tf.get_variable("wsim1", shape=[self.key_vec_size], dtype=tf.float32,
                                    initializer=tf.contrib.layers.xavier_initializer())
            # shape : 2*Hidden_size
            wsim2 = tf.get_variable("wsim2", shape=[self.key_vec_size], dtype=tf.float32,
                                    initializer=tf.contrib.layers.xavier_initializer())
            # shape : 2*Hidden_size
            wsim3 = tf.get_variable("wsim3", shape=[self.key_vec_size], dtype=tf.float32,
                                    initializer=tf.contrib.layers.xavier_initializer())
            # shape : (batch_size, num_keys, 1, key_vec_size)
            expanded_keys = tf.expand_dims(keys, 2)
            # shape : (batch_size, 1, num_values, value_vec_size)
            expanded_values = tf.expand_dims(values, 1)
            # shape :  (batch_size, num_keys, 1)
            sim1 = tf.reduce_sum(tf.multiply(expanded_keys, wsim1), axis=3)
            # shape :  (batch_size, 1, num_values)
            sim2 = tf.reduce_sum(tf.multiply(expanded_values, wsim2), axis=3)
            # shape : (batch_size, 1, num_values)
            intermediate_sim1 = tf.reduce_sum(tf.multiply(expanded_values, wsim3), axis=3)
            # shape : (batch_size, num_keys, 1)
            intermediate_sim2 = tf.reduce_sum(tf.multiply(expanded_keys, wsim3), axis=3)
            # shape : (batch_size, num_keys, num_values)
            sim3 = tf.multiply(intermediate_sim2, intermediate_sim1)

            # shape : (batch_size, num_keys, num_values)
            sim = sim1 + sim2 + sim3

            attn_logits_mask = tf.expand_dims(values_mask, 1) # shape (batch_size, 1, num_values)
            _, c2q_attn_dist = masked_softmax(sim, attn_logits_mask, 2) # shape (batch_size, num_keys, num_values). take softmax over values
            # shape : batch_size, num_keys, value_vec_size
            alpha = tf.matmul(c2q_attn_dist, values)

            # shape : (batch_size, num_keys)
            m = tf.reduce_max(sim, axis=2)

            _, q2c_attn_dist = masked_softmax(m, keys_mask, 1)  # shape (batch_size, num_keys). take softmax over keys
            # (batch_size, 1, key_vec_size)
            c = tf.transpose(tf.matmul(tf.transpose(keys, perm=[0, 2, 1]), tf.expand_dims(q2c_attn_dist, axis=2)), perm=[0, 2, 1])

            # shape : (batch_size, num_keys, key_vec_size)
            keymulc = tf.multiply(keys, c)

            # shape : batch_size, num_keys, 3*key_vec_size
            output = tf.concat([alpha, tf.multiply(keys, alpha), keymulc], axis = 2)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return q2c_attn_dist, output

class MultiRNNEncoder(object):
    """
    General-purpose module to encode a sequence using a RNN.
    It feeds the input through a RNN and returns all the hidden states.

    Note: In lecture 8, we talked about how you might use a RNN as an "encoder"
    to get a single, fixed size vector representation of a sequence
    (e.g. by taking element-wise max of hidden states).
    Here, we're using the RNN as an "encoder" but we're not taking max;
    we're just returning all the hidden states. The terminology "encoder"
    still applies because we're getting a different "encoding" of each
    position in the sequence, and we'll use the encodings downstream in the model.

    This code uses a bidirectional GRU, but you could experiment with other types of RNN.
    """

    def __init__(self, hidden_size, keep_prob, num_layers):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.keep_prob = keep_prob
        self.num_layers = num_layers
        self.rnn_cell_fw = [rnn_cell.GRUCell(self.hidden_size) for _ in range(self.num_layers)]
        self.rnn_cell_fw = [DropoutWrapper(cell, input_keep_prob=self.keep_prob) for cell in self.rnn_cell_fw]
        self.multi_rnn_cell_fw = rnn_cell.MultiRNNCell(self.rnn_cell_fw, state_is_tuple=False)

        self.rnn_cell_bw = [rnn_cell.GRUCell(self.hidden_size) for _ in range(self.num_layers)]
        self.rnn_cell_bw = [DropoutWrapper(cell, input_keep_prob=self.keep_prob) for cell in self.rnn_cell_bw]
        self.multi_rnn_cell_bw = rnn_cell.MultiRNNCell(self.rnn_cell_bw, state_is_tuple=False)

    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        with vs.variable_scope("MultiRNNEncoder"):

            # Note: The bidirectional_dynamic_rnn needs the actual size of the input which can be found be summing
            # the masks (as it has 1s for every valid input).
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # Note: fw_out and bw_out are the hidden states for every timestep.
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.multi_rnn_cell_fw, self.multi_rnn_cell_bw, inputs, input_lens, dtype=tf.float32)

            # Concatenate the forward and backward hidden states
            # shape is (batch_size, seq_len, 2*hidden_size)
            out = tf.concat([fw_out, bw_out], 2)

            # Apply dropout
            out = tf.nn.dropout(out, self.keep_prob)

            return out
            
def masked_softmax(logits, mask, dim):
    """
    Takes masked softmax over given dimension of logits.

    Inputs:
      logits: Numpy array. We want to take softmax over dimension dim.
      mask: Numpy array of same shape as logits.
        Has 1s where there's real data in logits, 0 where there's padding
      dim: int. dimension over which to take softmax

    Returns:
      masked_logits: Numpy array same shape as logits.
        This is the same as logits, but with 1e30 subtracted
        (i.e. very large negative number) in the padding locations.
      prob_dist: Numpy array same shape as logits.
        The result of taking softmax over masked_logits in given dimension.
        Should be 0 in padding locations.
        Should sum to 1 over given dimension.

        Here's what happens -
        1. Create an exp mask which is -large for 0 and 0 for 1.
        2. Add this to logits and call this newlogits.
        3. Take softmax of newlogits.
        4. e ^ (-large) = 0 (Genius!!)
    """
    exp_mask = (1 - tf.cast(mask, 'float')) * (-1e30) # -large where there's padding, 0 elsewhere
    masked_logits = tf.add(logits, exp_mask) # where there's padding, set logits to -large
    prob_dist = tf.nn.softmax(masked_logits, dim)
    return masked_logits, prob_dist


