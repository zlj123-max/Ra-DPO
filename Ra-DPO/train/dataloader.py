# Copyright (c) 2023 Contextual AI, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
"""
Contains the functions for loading data.
Each function of the form get_{dataset_name} (e.g., get_shp, get_oasst, etc.) will return a dict of Example objects, indexed by the prompt for the text.

Each Example object will contain
- the prompt
- a list L of generations
- the index in L of the generation that should be the finetuning target
- a list S of the scores for the generations
- for binary feedback data: pairs of indices (i,j) in L, where generation i is preferable to generation j
- for unary feedback data: whether each generation is desirable/chosen or undesirable/rejected
- whether to truncate the beginning or end if the maximum number of tokens is exceeded
- the dataset name
- the unformatted prompt
"""

import datasets
import torch
from torch.nn.utils.rnn import pad_sequence
from collections import defaultdict
import tqdm
import re
import random
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from .utils import rank0_print, on_rank0, delete_dict
import pandas as pd
import numpy as np


@dataclass
class Example:
    """
    Class for an example in a preference or SFT dataset. If you want each prompt to be uniquely associated with an Example instance, save it in a dict.
    """
    prompt: str = ''                                            # prompt for the generated texts
    generations: List[str] = field(default_factory=list)        # list of generations
    sft_index: int = -1                                         # which response in generations should be generated for SFT
    scores: List[float] = field(default_factory=list)           # score for each generation
    pairs: List[Tuple[int, int]] = field(default_factory=list)  # for binary feedback data:: indices in responses, where i > j in pair (i,j) is a preference
    desirable: List[bool] = field(default_factory=list)         # for unary feedback data: whether the generation at the corresponding index in self.generations is desirable 
    truncation_mode: str = 'keep_end'                           # if truncation needed, keep the beginning (keep_start) or end (keep_end) (only override default for SHP)
    dataset_name: str = ''
    original_prompt: str = ''                                   # the unformatted prompt (needed to recover instruction for AlpacaEval)

    def num_generations(self):
        return len(self.generations)
    
    def remove_extra_spaces(self):
        """
        Remove double spaces in the prompt and generations to standardize spacing.
        """
        def clean(text: str) -> str:
            return re.sub(r'[ \t]{2,}', ' ', text)

        # Clean the prompt
        for turn in self.prompt:
            turn['content'] = clean(turn['content'])

        # Clean the generations
        self.generations = [clean(gen) for gen in self.generations]


class Dataset:
    """
    A collection of Example instances, indexed by prompt.
    """
    def __init__(self, name):
        self.name = name
        self.data = defaultdict(Example)

    def __setitem__(self, key, value):
        if not isinstance(key, str):
            raise KeyError("key must be a string")
        
        if not isinstance(value, Example):
            raise ValueError("value must be a Example")
        
        self.data[key] = value

    def __getitem__(self, key):
        return self.data[key]

    def __len__(self):
        return len(self.data)
    
    def __iter__(self):
        return iter(self.data)


def get_imdb(split: str) -> Dataset:
    """
    Load the imdb dataset (for evaluation only) and convert it into a Dataset.

    Args:
        - split: must be 'test'; otherwise error will be thrown

    Returns:   
        A Dataset instance.
    """

    rank0_print(f'Loading imdb dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('stanfordnlp/imdb', split=split)
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing imdb')

    data = Dataset('imdb')

    for row in dataset:
        conversation = [{"role": "user", "content": row['text']}]  # Exclude the current message
        
        # print("row",row)
        # if row['label']==1.0:
        #     pos_class = row['label']
        # else:
        #     neg_class = row['label']

        # # Create a unique key for this example (using the prompt)
        key = row['text']
        # print("row['label']",row['label'])

        # Update the dataset
        data[key].prompt = conversation
        data[key].generations.extend(row['text'])
        i, j = data[key].num_generations() - 2, data[key].num_generations() - 1
        data[key].pairs.append((i, j))
        data[key].sft_index = i  # The chosen response is the SFT target
        data[key].dataset_name = 'imdb'
        data[key].truncation_mode = 'keep_start'
        data[key].remove_extra_spaces()

    return data


def get_alpacaeval(split: str) -> Dataset:
    """
    Load the AlpacaEval dataset (for evaluation only) and convert it into a Dataset.

    Args:
        - split: must be 'test'; otherwise error will be thrown

    Returns:   
        A Dataset instance.
    """
    if split == 'test':
        split = 'eval'
    else:
        raise ValueError('alpacaeval is only for evaluation')

    rank0_print(f'Loading AlpacaEval dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('tatsu-lab/alpaca_eval', split=split)
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing AlpacaEval')

    data = Dataset('alpacaeval')

    for row in dataset:
        conversation = [{"role": "user", "content": row['instruction']}]
        data[row['instruction']].prompt = conversation
        data[row['instruction']].generations.append(row['output'])
        data[row['instruction']].dataset_name = row['dataset']
        data[row['instruction']].original_prompt = row['instruction']
        data[row['instruction']].sft_index = 0

    return data


def get_shp(split: str, seed: int=0) -> Dataset:
    """
    Load the Stanford Human Preferences dataset from Huggingface and convert it into to a Dataset.

    We filter preference pairs to only keep pairs where the score ratio is at least 2 (as in original SHP).
    For this dataset, the SFT text is the first response in SHP for a given prompt. 
    This is because the globally best response cannot be inferred from SHP, but all responses are a good option because they have a positive score.
    """
    MAX_PAIRS_PER_PROMPT = 5
    MIN_SCORE_RATIO = 2

    rank0_print(f'Loading SHP dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('stanfordnlp/SHP', split=split)
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing SHP')

    data = Dataset('shp')

    for row in dataset:
        conversation = [{"role": "user", "content": row['history']}]
        responses = [row['human_ref_A'], row['human_ref_B']]
        scores = [row['score_A'], row['score_B']]
        score_ratio = max(scores[0] / scores[1], scores[1] / scores[0])

        if score_ratio < MIN_SCORE_RATIO and split == 'train':
            continue

        i, j = data[row['history']].num_generations(), data[row['history']].num_generations() + 1
        data[row['history']].prompt = conversation
        data[row['history']].original_prompt = row['history']
        data[row['history']].generations.extend(responses)
        data[row['history']].pairs.append((i, j) if row['labels'] == 1 else (j, i))
        data[row['history']].scores.extend(scores)
        data[row['history']].truncation_mode = 'keep_start'  # keep start for SHP because it's single-turn with long prompts
        data[row['history']].sft_index = 0  # absolute best response cannot be inferred, so just pick the first
        data[row['history']].dataset_name = 'shp'
        data[row['history']].remove_extra_spaces()

    # prevent over-fitting
    if split == 'train':
        for prompt in data:
            data[prompt].pairs = random.Random(seed).sample(data[prompt].pairs, min(MAX_PAIRS_PER_PROMPT, len(data[prompt].pairs)))

    return data


def get_hh(split: str, only_helpful=False, only_harmless=False) -> Dataset:
    """
    Load the Anthropic Helpful-Harmless dataset from Huggingface and convert it into to a Dataset.
    For this dataset, the SFT text is the preferred response.
    
    Args:
        - split: one of 'test', 'train'
        - only_helpful: only the helpfulness data
        - only_harmless: only the harmlessness data

    Returns:   
        A Dataset instance.
    """
    if only_helpful:
        dataset = datasets.load_dataset('Anthropic/hh-rlhf', split=split, data_dir="helpful-base")
        data = Dataset('Anthropic-HH-helpful')
    elif only_harmless:
        dataset = datasets.load_dataset('Anthropic/hh-rlhf', split=split, data_dir="harmless-base")
        data = Dataset('Anthropic-HH-harmless')
    else:
        rank0_print(f'Loading HH dataset ({split} split) from Huggingface...')
        dataset = datasets.load_dataset('Anthropic/hh-rlhf', split=split)
        data = Dataset('Anthropic-HH')
        
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing HH')

    def split_prompt_and_responses(ex):
        parts = re.split(r'\n\nHuman: |\n\nAssistant: ', ex['chosen'])
        conversation = []
        for i, part in enumerate(parts[1:]):  # Skip the first empty part
            role = "user" if i % 2 == 0 else "assistant"
            conversation.append({"role": role, "content": part.strip()})
        chosen_response = conversation.pop()['content']
        rejected_response = ex['rejected'].split('\n\nAssistant: ')[-1].strip()
        return conversation, chosen_response, rejected_response

    for row in dataset:
        conversation, chosen, rejected = split_prompt_and_responses(row)
        prompt_key = ' '.join([turn['content'] for turn in conversation])  # Use full conversation as key
        responses = [chosen, rejected]
        i, j = data[prompt_key].num_generations(), data[prompt_key].num_generations() + 1

        data[prompt_key].prompt = conversation
        data[prompt_key].generations.extend(responses)
        data[prompt_key].pairs.append((i, j))
        data[prompt_key].sft_index = 0

        if only_helpful:
            data[prompt_key].dataset_name = 'hh_helpful'
        elif only_harmless:
            data[prompt_key].dataset_name = 'hh_harmless'
        else:
            data[prompt_key].dataset_name = 'hh'

        data[prompt_key].remove_extra_spaces()

    return data


def get_hh_helpful(split: str) -> Dataset:
    return get_hh(split, only_helpful=True)


def get_hh_harmless(split: str) -> Dataset:
    return get_hh(split, only_harmless=True)


def get_oasst(split: str) -> Dataset:
    """
    Load the Open Assistant dataset from Huggingface and convert it into to a Dataset.
    For this dataset, the SFT text is the preferred response.

    OASST is a dataset of ranked responses (not just pairwise), but since we are working with losses that expect paired preferences, 
    turn a ranking (a, b, c, d, e) into pairwise preferences ((a,b), (b,c), (c,d), (d,e)).
    
    Args:
        - split: one of 'test', 'train'

    Returns:   
        A Dataset instance.
    """
    rank0_print(f'Loading OASST dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('OpenAssistant/oasst1', split=('validation' if split == 'test' else 'train'))
    dataset = dataset.filter(lambda x: x['lang'] == 'en')

    message_indexed_df = pd.DataFrame(dataset).set_index('message_id')
    parent_indexed_df = pd.DataFrame(dataset).set_index('parent_id')

    def get_path_to_root(node: pd.Series):
        if node['parent_id'] is None:
            return [node]
        else:
            parent = message_indexed_df.loc[node['parent_id']]
            return [node] + get_path_to_root(parent)
    
    def build_conversation(path: List[pd.Series]):
        conversation = []
        for node in reversed(path):
            role = "user" if node['role'] == 'prompter' else "assistant"
            conversation.append({"role": role, "content": node['text']})
        return conversation

    data = Dataset('OASST')

    for row in (tqdm.tqdm(dataset, desc='Processing OASST') if on_rank0() else dataset):
        if row['rank'] == 0 or row['rank'] is None:
            continue

        try:
            sibling_df = parent_indexed_df.loc[row['parent_id']]
            next_best_sibling = sibling_df[sibling_df['rank'] == (row['rank'] - 1)].iloc[0]
            path_to_root = get_path_to_root(message_indexed_df.loc[next_best_sibling['message_id']])
        except KeyError:
            continue
        except IndexError:
            continue

        conversation = build_conversation(path_to_root[1:])  # Exclude the current message
        prompt_key = json.dumps(conversation)  # Use the conversation as the key

        data[prompt_key].prompt = conversation
        data[prompt_key].generations.extend([next_best_sibling['text'], row['text']])
        data[prompt_key].pairs.append((len(data[prompt_key].generations) - 2, len(data[prompt_key].generations) - 1))
        data[prompt_key].scores.extend([next_best_sibling['rank'], row['rank']])
        data[prompt_key].dataset_name = 'oasst'
        data[prompt_key].remove_extra_spaces()
    
    return data


def get_ultrabin(split: str) -> Dataset:
    """
    Load the Ultrafeedback (binarized) dataset from Huggingface and convert it into to a Dataset.
    For this dataset, the SFT text is the preferred response.

    Args:
        - split: one of 'test', 'train'

    Returns:   
        A Dataset instance.
    """
    if split == 'train':
        split = 'train_prefs'
    elif split == 'test':
        split = 'test_prefs'
    else:
        raise ValueError("Split must be either 'train' or 'test'")
    
    rank0_print(f'Loading Ultra Binarized dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('HuggingFaceH4/ultrafeedback_binarized', split=split)
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing Ultrachat Binarized')

    data = Dataset('ultrabin')

    for row in dataset:
        # Convert the prompt into the new format
        conversation = [{"role": "user", "content": row['prompt']}]

        # Get the chosen and rejected responses
        chosen_response = row['chosen'][-1]['content']
        rejected_response = row['rejected'][-1]['content']

        # Create a unique key for this example (using the prompt)
        key = row['prompt']

        # Update the dataset
        data[key].prompt = conversation
        data[key].generations.extend([chosen_response, rejected_response])
        i, j = data[key].num_generations() - 2, data[key].num_generations() - 1
        data[key].pairs.append((i, j))
        data[key].sft_index = i  # The chosen response is the SFT target
        data[key].dataset_name = data.name
        data[key].truncation_mode = 'keep_start'
        data[key].remove_extra_spaces()

    return data


def get_ultrafeedback_hybrid(split: str) -> Dataset:
    rank0_print(f'Loading ultrafeedback_hybrid dataset ({split} split) from Huggingface...')
    dataset = datasets.load_dataset('wzhouad/gemma-2-ultrafeedback-hybrid', split=split)
    if on_rank0():
        dataset = tqdm.tqdm(dataset, desc='Processing ultrafeedback hybrid')

    data = Dataset('ultrafeedback_hybrid')

    for row in dataset:
        # Convert the prompt into the new format
        conversation = [{"role": "user", "content": row['prompt']}]

        # Get the chosen and rejected responses
        chosen_response = row['chosen'][-1]['content']
        rejected_response = row['rejected'][-1]['content']

        # Create a unique key for this example (using the prompt)
        key = row['prompt']

        # Update the dataset
        data[key].prompt = conversation
        data[key].generations.extend([chosen_response, rejected_response])
        i, j = data[key].num_generations() - 2, data[key].num_generations() - 1
        data[key].pairs.append((i, j))
        data[key].sft_index = i  # The chosen response is the SFT target
        data[key].dataset_name = data.name
        data[key].truncation_mode = 'keep_start'
        data[key].remove_extra_spaces()

    return data


class DataLoader:
    """
    The base data loader class, similar to the one from the DPO repo.
    Subclass this and overwrite the __iter__ method as needed, since the batcch elements will be different depending
    on whether you're doing SFT, aligning with a pairwise loss like DPO, or alignment with a unary loss like KTO. 
    """
    def __init__(self, 
                 dataset_names: List[str],
                 tokenizer,
                 split: str = 'train',
                 batch_size: int = 1,
                 max_length: int = 512,
                 max_prompt_length: int = 128,
                 max_prompt_count: int = None,
                 n_epochs: Optional[int] = None,
                 n_examples: Optional[int] = None,
                 seed: int = 0,
                 control_tokens: Dict = {},
                 **kwargs):
        
        torch.manual_seed(seed)
        self.seed = seed
        self.tokenizer = tokenizer
        self.control_tokens = control_tokens
        self.split = split
        self.batch_size = batch_size
        self.max_length = max_length
        self.max_prompt_length = max_prompt_length
        self.max_prompt_count = max_prompt_count
        self.kwargs = kwargs

        assert n_epochs is not None or n_examples is not None, "Must specify either n_epochs or n_examples"
        self.n_epochs = n_epochs
        self.epoch_idx = 0
        self.n_examples = n_examples
        
        self.full_data = {} # a dict of Examples

        for name in dataset_names:
            dataset = globals()[f"get_{name}"](split)
            self.full_data.update(dataset.data)

        self.num_training_steps = self.get_num_training_steps()

    def collate(self, batch: Dict[str, List]) -> Dict:
        """
        Takes a list of examples (dicts, where values are lists of ints [tokens] or strings [the original texts]) and returns a batch of examples,
        PyTorch tensors padded to the maximum length. Strings are passed through.
        """
        if self.tokenizer.pad_token_id is None:
            raise Exception("tokenizer's pad_token_id is not specified")
        
        # first, pad everything to the same length
        padded_batch = {}
        for k in batch[0].keys():
            if k.endswith('_input_ids') or k.endswith('_attention_mask') or k.endswith('_labels'):
                if 'prompt' in k:
                    # flip prompt so that you are padding to the beginning
                    to_pad = [torch.LongTensor(ex[k][::-1]) for ex in batch]
                else:
                    to_pad = [torch.LongTensor(ex[k]) for ex in batch]

                if k.endswith('_input_ids'):
                    padding_value = self.tokenizer.pad_token_id
                elif k.endswith('_labels'):
                    padding_value = -100
                elif k.endswith('_attention_mask'):
                    padding_value = 0
                else:
                    raise ValueError(f"Unexpected key in batch '{k}'")

                padded_batch[k] = pad_sequence(to_pad, batch_first=True, padding_value=padding_value)
                if 'prompt' in k:
                    padded_batch[k] = padded_batch[k].flip(dims=[1])
            else:
                padded_batch[k] = [ex[k] for ex in batch]

        return padded_batch

    def tokenize_batch_element(self, conversation: List[Dict[str, str]], generation: str, truncation_mode: str, prefix: str='target') -> Dict:
        """
        Tokenize a single batch element and truncate if prompt + generation is too long. Batch element is turned into Pytorch 
        tensors in self.collate. Create the labels for the generation, which are of length equal to the sum of the length of 
        the prompt and the generation, with -100 for the prompt tokens.

        Args:
        - conversation: list of previous turns, each resembling dict {"role": "assistant", "content": generation}
        - generation: output text (i.e., assistant generation)
        - truncation_mode: one of 'keep_start'/'keep_end' (truncate end/beginning of prompt respectively)
        - prefix: the prefix corresponding to the generation (e.g., 'chosen', 'rejected', 'target')

        Returns:
            A dict of the tokenized prompt and the concatenation of the two on all relevant elements (e.g., tokens, 
            attention mask, etc.). The generation elements will have keys starting with '{prefix}_' and the concatenated 
            elements will have keys starting with '{prefix}_combined_'.
        """
        untruncated_prompt_string = self.tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True) # for inference-time generation
        
        filter_out_bos_eos = lambda x: [ t for t in x if t not in [ self.tokenizer.bos_token_id, self.tokenizer.eos_token_id, self.tokenizer.pad_token_id] ]
        # truncate the prompt if necessary
        prompt_length = 0

        # truncate history to fit in self.max_prompt_length
        for i, turn in enumerate(conversation):
            content_token_ids = filter_out_bos_eos(self.tokenizer.encode(turn['content']))
            # we're only modifying the text in content but need to consider the formatted length
            templated_length = len(self.tokenizer.apply_chat_template([turn], tokenize=True, add_generation_prompt=True))
            
            if prompt_length + templated_length > self.max_prompt_length:
                turn['content'] = self.tokenizer.decode(content_token_ids[:self.max_prompt_length - (prompt_length + templated_length)])
                prompt_length = self.max_prompt_length
                break
            else:
                prompt_length += templated_length

        conversation = conversation[:(i+1)]

        # truncate the generation if necessary 
        generation_token_ids = filter_out_bos_eos(self.tokenizer.encode(generation))
        generation = self.tokenizer.decode(generation_token_ids[:(self.max_length - prompt_length)])

        tokenized_prompt = self.tokenizer.apply_chat_template(conversation, tokenize=True, add_generation_prompt=True)
        if tokenized_prompt[-1] in [self.tokenizer.eos_token_id, self.tokenizer.pad_token_id]:
            tokenized_prompt.pop()
        
        tokenized_prompt_and_generation_string = self.tokenizer.apply_chat_template(conversation + [{"role": "assistant", "content": generation}], tokenize=False, add_generation_prompt=False)
        tokenized_prompt_and_generation = self.tokenizer.apply_chat_template(
            conversation + [{"role": "assistant", "content": generation}], 
            tokenize=True, 
            add_generation_prompt=False
        )

        # Prepare the batch element
        batch_element = {
            'prompt_text': untruncated_prompt_string,
            f'{prefix}_text': generation,
            f'{prefix}_combined_text': tokenized_prompt_and_generation_string,
            f'{prefix}_combined_input_ids': tokenized_prompt_and_generation,
            f'{prefix}_combined_attention_mask': [1] * len(tokenized_prompt_and_generation),
        }

        # Prepare labels
        labels = tokenized_prompt_and_generation[:]
        labels[:len(tokenized_prompt)] = [-100] * len(tokenized_prompt)
        batch_element[f'{prefix}_labels'] = labels

        return batch_element

    def __iter__(self):
        """Create a flat version of the data and yield batches."""
        raise NotImplementedError

    def get_num_training_steps(self):
        """Get the number of training steps."""
        raise NotImplementedError
    

class SFTDataLoader(DataLoader):
    """
    Dataloader for supervised fine-tuning.
    """
    def __iter__(self):
        flat_data = []
        prompts = list(self.full_data.keys())
        random.Random(self.seed).shuffle(prompts) # otherwise, will be frontloaded with prompts in same domain

        for prompt in prompts:
            flat_data.append(self.full_data[prompt])

        epoch_idx = 0
        example_idx = 0
        done = False
        
        while True:
            if done: break
            random.Random(self.seed + epoch_idx).shuffle(flat_data)

            batch = []

            for example in flat_data:
                # Assuming example.prompt is now a list of conversation turns
                conversation = example.prompt
                if not isinstance(conversation[0], dict):
                    # Convert to the new format if it's not already
                    conversation = [{"role": "user", "content": conversation[0]}]
                    for i, message in enumerate(conversation[1:]):
                        role = "assistant" if i % 2 == 0 else "user"
                        conversation.append({"role": role, "content": message})

                # Get the target generation (last turn from assistant)
                target_generation = example.generations[example.sft_index]

                # Add control token if specified
                if self.control_tokens.get('chosen'):
                    target_generation = self.control_tokens['chosen'] + target_generation

                batch_element = self.tokenize_batch_element(
                    conversation,
                    target_generation,
                    example.truncation_mode
                )
                batch_element['original_prompt'] = example.original_prompt
                batch.append(batch_element)

                if len(batch) == self.batch_size:
                    example_idx += len(batch)
                    yield self.collate(batch)
                    batch = []

                    if self.n_examples is not None and example_idx >= self.n_examples:
                        rank0_print(f'Finished generating {self.n_examples} examples on {self.split} split')
                        done = True
                        break

            epoch_idx += 1
            if self.n_epochs is not None and epoch_idx >= self.n_epochs:
                done = True
                break

    def get_num_training_steps(self):
        """Get the number of training steps."""
        return len(self.full_data)


class ConditionalSFTDataLoader(DataLoader):
    """
    Dataloader for token-conditioned SFT, in the style of Korbak et al.'s (2023) "Pretraining Models with Human
    Feedback."

    For training, each output is prepended with a control token denoting whether it's desirable or undesirable
    (<|good|> or <|bad|> respectively). For sampling, each input is postpended with the <good> token to ensure
    that only desirable outputs are generated.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.control_tokens.get('chosen') is None:
            raise KeyError("control token for chosen outputs not specified")
        
        if self.control_tokens.get('rejected') is None:
            raise KeyError("control token for rejected outputs not specified")

    def get_flat_data(self, prompts):
        """
        Return a flat list of examples given a list of prompts that index self.full_data.
        Prepend the examples with the appropriate control tokens.
        """
        flat_data = []

        for prompt in prompts:
            example = self.full_data[prompt]

            if self.max_prompt_count:
                example.pairs = random.Random(self.seed).sample(example.pairs, min(self.max_prompt_count, len(example.pairs)))

            for i,j in example.pairs:
                flat_data.append((example, example.generations[i], 'chosen'))
                flat_data.append((example, example.generations[j], 'rejected'))

        return flat_data
    
    def __iter__(self):
        prompts = list(self.full_data.keys()) 
        random.Random(self.seed).shuffle(prompts) # otherwise, will be frontloaded with prompts in same domain
        flat_data = self.get_flat_data(prompts)
      
        epoch_idx = 0
        example_idx = 0
        done = False
        
        while True:
            if done: break
            random.Random(self.seed + epoch_idx).shuffle(flat_data)

            batch = []

            for example, generation, status in flat_data:
                # Convert prompt to conversation format if it's not already
                conversation = example.prompt
                if not isinstance(conversation[0], dict):
                    conversation = [{"role": "user", "content": conversation[0]}]
                    for i, message in enumerate(conversation[1:]):
                        role = "assistant" if i % 2 == 0 else "user"
                        conversation.append({"role": role, "content": message})

                # Add control token to the generation
                if status == 'chosen':
                    conditioned_generation = self.control_tokens["chosen"] + generation
                else:
                    conditioned_generation = self.control_tokens["rejected"] + generation

                batch_element = self.tokenize_batch_element(
                    conversation,
                    conditioned_generation,
                    example.truncation_mode
                )
                batch_element['status'] = status
                batch.append(batch_element)

                if len(batch) >= self.batch_size:
                    example_idx += len(batch)
                    yield self.collate(batch)
                    batch = []

                    if self.n_examples is not None and example_idx >= self.n_examples:
                        rank0_print(f'Finished generating {example_idx} examples on {self.split} split')
                        done = True
                        break

            epoch_idx += 1
            if self.n_epochs is not None and epoch_idx >= self.n_epochs:
                done = True
                break

    def get_num_training_steps(self):
        max_prompt_count = min(float("inf"), self.max_prompt_count) if self.max_prompt_count else float("inf")
        num_pairs = int(sum(min(max_prompt_count, len(example.pairs)) for _, example in self.full_data.items()))
        num_training_steps = num_pairs * 2
        return num_training_steps


class UnpairedPreferenceDataLoader(DataLoader):
    """
    Dataloader for losses that do not require pairwise preferences (e.g., KTO).

    Since all the datasets have (or imply) pairwise preferences, this function assumes all preferred/dispreferred
    generations are from the desirable/undesirable conditional generations given x. 
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.batch_size == 1:
            raise ValueError("can't use batch size of 1 with UnpairedPreferenceDataLoader")
        
    def get_flat_data(self, prompts):
        """
        Return a flat list of examples given a list of prompts that index self.full_data.
        """
        if self.max_prompt_count:
            num_unique = sum(min(self.max_prompt_count, len(self.full_data[prompt].pairs)) for prompt in prompts)
        else:
            num_unique = sum(len(self.full_data[prompt].pairs) for prompt in prompts)

        allowed_desirable = num_unique * self.kwargs.get('frac_unique_desirable', 1.0)
        allowed_undesirable = num_unique * self.kwargs.get('frac_unique_undesirable', 1.0)
        seen_desirable = 0
        seen_undesirable = 0

        flat_data = []

        for prompt in prompts:
            example = self.full_data[prompt]

            if self.max_prompt_count:
                example.pairs = random.Random(self.seed).sample(example.pairs, min(self.max_prompt_count, len(example.pairs)))

            for i,j in example.pairs:
                if seen_desirable < allowed_desirable:
                    flat_data.append((example, example.generations[i], 'chosen'))
                    seen_desirable += 1
                
                if seen_undesirable < allowed_undesirable:
                    flat_data.append((example, example.generations[j], 'rejected'))
                    seen_undesirable += 1

        return flat_data

    def __iter__(self):
        prompts = sorted(list(self.full_data.keys()))
        random.Random(self.seed).shuffle(prompts) # otherwise, will be frontloaded with prompts in same domain
        flat_data = self.get_flat_data(prompts)

        epoch_idx = 0
        example_idx = 0
        done = False

        while True:
            if done: break
            random.Random(self.seed + epoch_idx).shuffle(flat_data)   # so generations in the same preference are not in the same batch
            batch = []
            example_queue = []

            for example, generation, status in flat_data:
                batch_element = self.tokenize_batch_element(example.prompt, generation, example.truncation_mode, prefix='target')
                batch_element['status'] = status 
                batch_element['truncation_mode'] = example.truncation_mode
                batch_element['conversation'] = example.prompt
                example_queue.append(batch_element)
                
                if len(example_queue) >= self.batch_size:
                    while len(batch) < self.batch_size:
                        batch.append(example_queue.pop(0))
                    
                if len(batch) >= self.batch_size:
                    # for estimating the KL term, match up x and y' that are not corresponding input-output pairs in the data
                    # for x_i, get a mismatched y' by just picking the subsequent y_{i+1} in the batch (desirable/undesirable status does not matter)
                    # the respective input IDs, attention mask, and so on will be prefixed by the term KL
                    indices = list(range(1, len(batch))) + [0]
                    for i in range(len(batch)):
                        batch[i].update(self.tokenize_batch_element(
                            batch[i]['conversation'],
                            batch[indices[i]]['target_text'],
                            batch[i]['truncation_mode'],
                            prefix='KL'
                        ))

                    example_idx += len(batch)
                    yield self.collate(batch)
                    batch = []

                    if self.n_examples is not None and example_idx >= self.n_examples:
                        rank0_print(f'Finished generating {example_idx} examples on {self.split} split')
                        done = True
                        break

            epoch_idx += 1
            if self.n_epochs is not None and epoch_idx >= self.n_epochs:
                done = True
                break

    def get_num_training_steps(self):
        max_prompt_count = min(float("inf"), self.max_prompt_count) if self.max_prompt_count else float("inf")
        num_pairs = int(sum(min(max_prompt_count, len(example.pairs)) for _, example in self.full_data.items()))
        num_training_steps = num_pairs * self.kwargs.get('frac_unique_desirable', 1.0) + num_pairs * self.kwargs.get('frac_unique_undesirable', 1.0)
        return num_training_steps


class ScoreUnaryDataLoader(UnpairedPreferenceDataLoader):
    def get_flat_data(self, prompts):
        """
        Return a flat list of examples given a list of prompts that index self.full_data.
        Assumes that there are a list of scores.
        """
        flat_data = []
        prev_status = 'rejected'

        for prompt in prompts:
            example = self.full_data[prompt]

            if self.max_prompt_count:
                example.pairs = random.Random(self.seed).sample(example.pairs, min(self.max_prompt_count, len(example.pairs)))

            # for oasst, lower scores are better, so rank 0 is the best response and rank n is the worst
            if prev_status == 'rejected':
                flat_data.append((example, example.generations[np.argmin(example.scores)], 'chosen'))
            else:
                flat_data.append((example, example.generations[np.argmax(example.scores)], 'rejected'))

            prev_status = flat_data[-1][-1]

        return flat_data


class PrefUnaryDataLoader(UnpairedPreferenceDataLoader):
    """
    Dataloader for training on only one output per input.
    This throws out at least half the data (more than half if there are multiple pairs per input).
    For this reason, this should ONLY be used for training.
    """
    def get_flat_data(self, prompts):
        """
        Return a flat list of examples given a list of prompts that index self.full_data.
        Only use one preference pair per input.
        """
        flat_data = []
        prev_status = 'rejected'

        for prompt in prompts:
            example = self.full_data[prompt]

            if self.max_prompt_count:
                example.pairs = random.Random(self.seed).sample(example.pairs, min(self.max_prompt_count, len(example.pairs)))

            for i,j in example.pairs:
                if prev_status == 'rejected':
                    flat_data.append((example, example.generations[i], 'chosen'))
                else:
                    flat_data.append((example, example.generations[j], 'rejected'))

                prev_status = flat_data[-1][-1]
                break # only use one pair

        return flat_data


class PairedPreferenceDataLoader(DataLoader):
    """
    Dataloader for losses that do require pairwise preferences (e.g., DPO).
    """
    def __iter__(self):
        flat_data = []
        prompts = list(self.full_data.keys())
        random.Random(self.seed).shuffle(prompts) # otherwise, will be frontloaded with prompts in same domain

        for prompt in prompts:
            example = self.full_data[prompt]

            if self.max_prompt_count:
                example.pairs = random.Random(self.seed).sample(example.pairs, min(self.max_prompt_count, len(example.pairs)))

            for pair in example.pairs:
                flat_data.append((example, pair))
         
        epoch_idx = 0
        example_idx = 0
        done = False

        while True:
            if done: break
            random.Random(self.seed + epoch_idx).shuffle(flat_data)
            batch = []

            for example, (i, j) in flat_data:
                batch_element = {}
                batch_element.update(self.tokenize_batch_element(example.prompt, example.generations[i], example.truncation_mode, prefix='chosen'))
                batch_element.update(self.tokenize_batch_element(example.prompt, example.generations[j], example.truncation_mode, prefix='rejected'))
                batch.append(batch_element)

                if len(batch) >= self.batch_size:
                    example_idx += len(batch)
                    yield self.collate(batch)
                    batch = []

                    if self.n_examples is not None and example_idx >= self.n_examples:
                        rank0_print(f'Finished {example_idx} examples on {self.split} split')
                        done = True
                        break

            epoch_idx += 1
            if self.n_epochs is not None and epoch_idx >= self.n_epochs:
                done = True
                break

    def get_num_training_steps(self):
        max_prompt_count = min(float("inf"), self.max_prompt_count) if self.max_prompt_count else float("inf")
        return int(sum(min(max_prompt_count, len(example.pairs)) for _, example in self.full_data.items()))
