# NeighbourScore-lite: constructing a neighbourhood predictor and interpreting its internal AUROC

NeighbourScore-lite combines fixed-radius neighbourhood expression summaries with standard L2-regularised logistic regression to predict a binary tissue label. Its reported AUROC of **0.95**, compared with **0.93** for expression-only logistic regression, describes one internal cell split. It does not establish performance in new donors: all three sets contain the same 12 donors, and label-based gene selection precedes the split.

M1 = [prediction-method.md](input/prediction-method.md), titled *虚构方法短文：NeighbourScore-lite for tissue label prediction*. The source explicitly identifies itself as original synthetic material, with no real paper, DOI or images. It provides no separate version identifier. All available text and both tables were read. Locations below use the actual section/table titles; no page numbers are invented.

## Inputs → construction → output

The method requires cell coordinates in a spatial scale that supports a **50 µm radius**, cell expression measurements, and binary tissue labels for supervised gene selection and fitting. Training labels should be distinguished from deployment inputs: a deployed predictor would need coordinates and expression, with feature selection and fitted coefficients fixed beforehand. The source does not specify the exact label definition, expression preprocessing or normalisation. [M1, Introduction; Results — Construction of the score; Methods.](input/prediction-method.md)

| Step | Reported operation and purpose | Output / information still needed |
|---|---|---|
| 1. Choose genes | Select the **200 genes most associated with the label**, using all 60,000 cells before partitioning | Selected gene list; the association statistic, tie handling and any preprocessing are not specified |
| 2. Define neighbours | For each cell, collect neighbours within a fixed **50 µm** radius | Neighbour set; distance convention, self-inclusion, tissue-edge handling and treatment of cells with no neighbours are not specified |
| 3. Aggregate expression | Compute the mean expression of each selected gene among those neighbours | Neighbourhood feature vector; captures surrounding expression while removing the arrangement of individual neighbours |
| 4. Combine features | Concatenate neighbourhood means with the cell's own expression | Model input vector; the source does not explicitly state whether the own-expression branch uses exactly the same 200 genes |
| 5. Fit the predictor | Use **L2-regularised logistic regression** | Coefficients and a binary-label probability; regularisation tuning and parameter settings are not reported |
| 6. Evaluate internally | Randomly split cells into 42,000 training, 9,000 validation and 9,000 test cells; compare with expression-only logistic regression on the same split | One held-out-cell AUROC per model, with donor membership shared across all sets |

Source for construction: M1, **Results — Construction of the score; Table 1 — Model components**. Source for ordering and sample partition: M1, **Methods**. The scientific presentation actually reports **Internal discrimination / Table 2 first, construction / Table 1 second**; the table above reconstructs an executable explanation rather than changing the source's Results order.

A mathematical schematic makes the operation explicit. Let Nᵢ be the implementation's set of neighbours within 50 µm, and xⱼg the expression of selected gene g in neighbour j. The neighbourhood mean is mᵢg = Σⱼ∈Nᵢ xⱼg / |Nᵢ|. Concatenating the own-expression vector with mᵢ gives zᵢ, and logistic regression maps it to pᵢ = 1 / [1 + exp(−(b + wᵀzᵢ))]. L2 regularisation discourages large fitted coefficients. These equations are **an explanatory reconstruction of the stated method**, not equations supplied by the source; the penalty strength and exact fitting implementation are unavailable. No model was fitted here.

The neighbour branch has one mean per selected gene, hence 200 such features. A total feature count cannot be confirmed without the own-expression specification. A probability is the output; no decision threshold or calibration evidence is supplied. The innovation described by the source is a combination of existing neighbourhood aggregation and logistic regression, not a new optimisation algorithm. [M1, Introduction; Table 1; Methods.](input/prediction-method.md)

## Assumptions and limits affecting usefulness

The radius assumes that a 50 µm neighbourhood is an appropriate scale for the classification task and that coordinates have comparable meaning across samples. Mean aggregation assumes the average is informative; it can mix cell identities or densities and loses directional and within-neighbourhood heterogeneity. Those are properties of this construction, not experimentally demonstrated failure modes in the source. There is no radius sensitivity analysis or decomposition of the improvement, so the source does not establish whether the gain reflects local biology, neighbour cell composition, donor structure or another correlated feature. [M1, Construction; Methods; Discussion.](input/prediction-method.md)

**Gene-selection leakage is explicit.** Selecting the 200 genes against labels on all cells exposes the feature-selection stage to validation and test labels before evaluating the model. The resulting AUROC cannot be treated as performance of a fully isolated test pipeline. Its degree of inflation cannot be quantified without new analyses. [M1, Methods.](input/prediction-method.md)

**The target of generalisation is mismatched.** The dataset has **12 donors and 60,000 cells**, and each split contains all 12 donors. The test asks how the predictor ranks held-out cells from represented donors, while the author claims usefulness in new donors. Cell counts do not create 60,000 independent donor observations. Spatially nearby cells may also share information; whether neighbourhood features span split boundaries or samples is unspecified, so additional spatial leakage remains a risk to investigate, not a verified implementation fact. [M1, Internal discrimination; Methods.](input/prediction-method.md)

The reported AUROC gain is **0.95 − 0.93 = 0.02 absolute AUROC units**, from one random split without confidence intervals or external testing. AUROC measures discrimination across thresholds; it is not accuracy, calibrated probability quality or clinical benefit. The same split helps comparison, but the unspecified tuning budget and absent feature ablations leave the source of the gain unresolved. Neither a statistically reliable improvement nor new-donor generalisation is established. [M1, Table 2 — Internal evaluation; Methods; Discussion.](input/prediction-method.md)

## Evidence record

| ID / claim or concern | Source and location | Comparison, denominator and unit | Actual check | Support boundary |
|---|---|---|---|---|
| E1: score construction | M1 Introduction; Construction; Table 1; Methods | 50 µm neighbour means, 200 selected genes, own-expression concatenation, L2 logistic regression | Read full text and Table 1; wrote a labelled mathematical schematic | Existing-method combination; exact preprocessing, neighbour conventions and fit parameters unreported |
| E2: internal discrimination | M1 Internal discrimination; Table 2; Methods | Neighbourhood+expression vs expression only; same one-time cell split; 9,000 test cells from 12 represented donors | Read Table 2 and split design; calculated 0.95 − 0.93 = 0.02 | No interval, repeated estimate, new-donor test or calibration; improvement is reported internal discrimination |
| E3: feature-selection leakage | M1 Methods | Label-based gene selection on all 60,000 cells before training/validation/test partition | Read and checked operation order | Test/validation labels influence selection; magnitude of bias not calculated |
| E4: new-donor claim | M1 Internal discrimination; Methods; Discussion | 12 donors in each of train/validation/test; no donor-group split or external cohort | Read donor membership and claim together | Held-out cells from known donors cannot demonstrate performance in unseen donors |
| E5: split sizes | M1 Methods | 42,000 / 9,000 / 9,000 cells | Calculated sum = 60,000 and fractions = 70% / 15% / 15% | Arithmetic check only; no partition file, fitting or metric reproduction |

## An evaluation that could support the intended use

For the stated new-donor goal, hold out complete donors and perform preprocessing choices, label-based gene selection and regularisation tuning using only the training portion of each evaluation fold. Apply the resulting fixed pipeline to held-out donors. Construct neighbourhoods within the biologically appropriate sample and state how the intended prediction setting handles held-out spatial measurements. Compare the same expression-only and neighbourhood models with matched tuning opportunities and report uncertainty appropriate to donor-level dependence.

To test whether neighbourhood information adds useful information, predefine radius sensitivity and feature ablations, and compare discrimination together with calibration in an independently collected donor cohort when feasible. Spatially blocked evaluation would answer a different question about new regions within samples; it should be labelled with that target. These are proposed validation designs prompted by E2–E4, not results obtained here.

## Actual scope and resources

I read the **entire supplied short methods text**, including Introduction, both Results subsections, Tables 1 and 2, Methods, Discussion and Data and code. This is a focused analysis of a method fragment whose whole content is relevant to its construction and usefulness; it is not a close reading of a longer paper. No image was supplied or viewed. I executed only the summary arithmetic above. No data or code are available in the supplied material, and no network access, download, fitting, runtime benchmark or raw-data reanalysis was performed. Software versions, hardware, running time and theoretical complexity are not reported; I cannot infer computational cost from the “lite” name.
