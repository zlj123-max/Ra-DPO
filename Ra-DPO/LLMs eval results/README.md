# NeurIPS 2025 Ra-DPO (Submission Number: 20057)

# An overview of the additional experiment results for "Risk-aware Direct Preference Optimization under Nested Risk Measure"

## Overall:

We add several experimental results to demonstrate the advantages of our method, specifically including:

1) **Numerical example:** includes a numerical example;
2) **Q&A  evaluation examples using LLMs:** presents comparative evaluation results of LLMs across different algorithms on Q&A datasets;

## Details:

### The "Numerical example" folder

* Figure 1 presents a simple example that demonstrates the characters of nested risk measures.

### The "Q&A  evaluation examples using LLMs" folder

* **Raw Q&A datasets:** contains several Q&A datasets, each corresponding to a different algorithm and sampled using AlpacaEval from the experiment conducted on the Anthropic HH dataset, with Pythia-1.4B serving as the base model.
* **Selected Q&A Datasets:** contains the Q&A datasets selected for evaluation.
* **Evaluation results of LLMs:** presents the comparative performance of DeepSeek and GPT-4o on our algorithm versus baseline methods, using a selected set of questions.

_**Conclusions(LLMs):**_ Some (Our) algorithms (such as HH_py1.4b_sft_ra-tdpo1_cl0.97 and HH_py1.4b_sft_ra-tdpo2_alp0.5_cl0.97) can provide high-quality practical information, while the effectiveness of other algorithms (such as HH_py1.4b_sft_ra-tdpo1_cl0.99 and HH_py1.4b_sft_ra-tdpo2_alp0.5_cl0.99) is relatively low and their redundancy is relatively high. Future research should focus on optimizing algorithms to improve their performance in terms of effectiveness and redundancy, thereby enhancing their overall performance.
