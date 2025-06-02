# Risk-aware Direct Preference Optimization (Ra-DPO) 😇

This repo allows you to align LLMs with various methods, such as DPO, KTO, PPO, TDPO and Ra-DPO.

Configs are handled by [Hydra](https://hydra.cc/), jobs are launched with [Accelerate](https://huggingface.co/docs/accelerate/en/index), and all training is done with FSDP by default. To first SFT a model from the Hugginface repo EleutherAI/pythia-1.4b, run a command like

```accelerate
accelerate launch --config_file accelerate_config/fsdp_4gpu.yaml --main_process_port 29500 launch.py loss=dpo model=pythia datasets=[hh] exp_name=HH_py14_sft_dpo run_count=run1 ++model.name_or_path=HH_pretrained_model/EleutherAI/pythia-1.4b ++model.load_from=.cache/EleutherAI/pythia-1_4b_sft/run1/FINAL 
```

which will save a model to .cache/EleutherAI/pythia-1_4b_sft/run1/FINAL. To then align the SFT model with Ra-DPO, run a command like

```
accelerate launch --config_file accelerate_config/fsdp_4gpu.yaml --main_process_port 29500 launch.py loss=ra-dpo model=pythia datasets=[hh] exp_name=HH_py14_sft_ra_dpo1_cl0.99 run_count=run1 ++model.name_or_path=HH_pretrained_model/EleutherAI/pythia-1.4b ++model.load_from=.cache/EleutherAI/pythia-1_4b_sft/run1/FINAL  ++loss.confidence_level=0.99
```

which will save a model to .cache/EleutherAI/HH_py14_sft_ra_dpo1_cl0.99/run1/FINAL.

## Quickstart

1. First, clone the repo and install the dependencies. This might take a while. The package versions are important---if you change them, there is no guarantee the code will run.

   ```console
   . install.sh
   ```
2. Determine whether you need a new dataset. If you have a dataset that you want to refer to as `foo` when you launch jobs, add a function called `get_foo` in `dataloader.py` that will return a `Dataset` instance. This function should have the following signature, where `split` should be either `train` or `test`:

   ``def get_foo(split: str, *args, **kwargs) -> Dataset:``

   Determine whether you need a new dataloader. Each loss in `config/loss/` has one corresponding dataloader; for Ra-DPO, it is `dataloader.PreferenceDataLoader`. You will probably not need to write a new dataloader unless you are doing something creative, like turning score-based data into preferences or binary feedback.
3. Determine whether you need a new trainer. In most cases, this will subclass either `UnpairedPreferenceTrainer` (i.e., KTO-style) or `PairedPreferenceTrainer` (i.e., Ra-DPO-style). If you need highly custom behavior that is not in either, then you can subclass `BasicTrainer` directly.

## FAQs

1. Do you support multi-node training?

   Yes, see the `launch_multinode_batch.sh` and `launch_multinode_interactive.sh` for how to launch jobs across two nodes in a batch or interactive Slurm job. You may need a custom Accelerate configuration depending on how many nodes you have. Use the 2-node examples in `accelerate_config` as a template.
2. How do I save intermediate checkpoints?

   Set `intermediate_checkpoints` to true in `config/config.yaml` or on the command line with `++config.intermediate_checkpoints=true`.
   Every `config.eval_every` steps, a checkpoint will be saved in the experiment directory ($cache_dir/$exp_name).
3. Where do I find all the Archangel models?

   They are all on the [Huggingface Hub](https://huggingface.co/collections/ContextualAI/archangel-65bd45029fa020161b052430).
4. Do you support LoRA training?

   Yes. Set `use_peft` to true in `config/model/base_model.yaml` or on the command line with `++model.use_peft=true`. You can either use the default LoRA hyperparameters in `config/model/base_model.yaml` or override them on the command line (e.g., `++model.peft.lora_r=128`). Note that intermediate checkpoints during LoRA training will only be the LoRA module, but the LoRA weights will be merged with the model before the final save.
5. Do you support FlashAttention?

   Yes, just override `attn_implementation` to `flash_attention_2` in `model/base_model.yaml`, on the command line, or in the any of the files that inherit from `model/base_model.yaml`. This is done by default for certain model classes.
6. Can I precompute the log probabilities of the reference model to save memory?

   Yes. Simply set `++cache_reference_logprobs=true` to precompute the log probabilities from the reference model, which will substantially reduce memory. If you are using the same reference model across multiple jobs, which is common, you can override `++reference model=PATH` to the log probabilities that were cached in a pickle file from a previous job.

## Citation

Our code builds upon the KTO code with improvements. Thanks to them! If you find this repo useful, please feel free to cite:

```
@inproceedings{ethayarajhmodel,
  title={Model Alignment as Prospect Theoretic Optimization},
  author={Ethayarajh, Kawin and Xu, Winnie and Muennighoff, Niklas and Jurafsky, Dan and Kiela, Douwe},
  booktitle={Forty-first International Conference on Machine Learning}
}
```
