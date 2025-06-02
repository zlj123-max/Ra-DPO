
## Comprehensive Evaluation of Algorithm Performance in Question-Answering （Q&A） Tasks

## Overall:

This folder includes three files:  Raw Q&A datasets, Selected Q&A datasets and Evaluation results of LLMs.

* **Raw Q&A datasets:** contains several Q&A datasets, each corresponding to a different algorithm and sampled using AlpacaEval from the experiment conducted on the Anthropic HH dataset, with Pythia-1.4B serving as the base model.
* **Selected Q&A Datasets:** contains the Q&A datasets that we have chosen to demonstrate the advantages of our algorithm.
* **Evaluation results of LLMs:** contains the evaluations of DeepSeek and GPT-4o for our algorithm and  baselines answers for a selected few questions.

Next, we will focus on the evaluation results of large models (see the "Evaluation results of LLMs" folder).

## Details:

### Evaluation Methods

To evaluate the performance of each algorithm, we selected five Q&A examples and conducted a comprehensive evaluation using Deepseek and GPT - 4o as evaluators. The evaluation dimensions include:

* **Riskiness:** Whether the answer has potential risks or problems.
* **Effectiveness:** Whether the answer can effectively solve the problem.
* **Relevance:** Whether the answer closely revolves around the core of the question.
* **Redundancy:** Whether the answer has unnecessary repetitions or redundant information.

**Note：**When using Deepseed or GPT-4o as evaluators, we start a new dialogue each time to avoid context interference and ensure the independence and reliability of the evaluation.

### Question Selection and Reasons

The selected questions cover multiple fields ranging from daily life skills to complex scientific ethics, including daily life skills, cutting-edge science and technology, workplace communication, modern work styles, and public health, which can reflect the performance of algorithms in different scenarios.

### Evaluation Documents and Results

The folder "The evaluation results" contains two.md files, namely "The evaluation results of DeepSeek.md" and "The evaluation results of GPT - 4o.md". They contain detailed evaluation results of various algorithms by DeepSeek and GPT - 4o, respectively.

### Results and Discussion

The evaluation results show that our algorithm (Ra-DPO) performs well in terms of riskiness and relevance, but there are differences in effectiveness and redundancy.

* Some algorithms (such as HH_py1.4b_sft_ra-tdpo1_cl0.97 and HH_py1.4b_sft_ra-tdpo2_alp0.5_cl0.97) can provide high-quality practical information, while the effectiveness of other algorithms (such as HH_py1.4b_sft_ra-tdpo1_cl0.99 and HH_py1.4b_sft_ra-tdpo2_alp0.5_cl0.99) is relatively low and their redundancy is relatively high.
* Future research should focus on optimizing algorithms to improve their performance in terms of effectiveness and redundancy, thereby enhancing their overall performance.
