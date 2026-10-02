# NeighbourScore-lite: construction, assumptions, and the limits of its reported usefulness

NeighbourScore-lite augments a cell's expression profile with the mean expression of nearby cells, then applies L2-regularized logistic regression to predict a binary tissue label. It is a combination of established feature aggregation and a standard classifier, rather than a new optimization algorithm. Its reported advantage comes from one internal evaluation whose feature selection uses all cells before splitting; that evaluation cannot establish performance in new donors. [M1: Introduction; Results — Construction of the score; Methods](input/prediction-method.md)

This is an English **focused method analysis** of the supplied fictional text. M1 denotes the complete local source, originally titled “虚构方法短文：NeighbourScore-lite for tissue label prediction.” It has no DOI, publication record, or images. The construction, evaluation, Methods, Introduction, Discussion, and Data and code sections were all read because they directly affect the requested method interpretation. This is analysis of the supplied description, not an implemented or independently reproduced model. [Source](input/prediction-method.md)

## What the method needs and produces

| Item | Role | What is specified and what remains unclear |
|---|---|---|
| Cell coordinates | Define the spatial neighborhood of each target cell | A fixed radius of 50 μm is specified. Coordinate dimensions, distance metric, specimen boundaries, and handling of edge cells are not reported. |
| Cell expression matrix | Supplies each cell's own expression and neighboring expression values | The text selects 200 genes. Gene identities, expression normalization, scaling, and missing-value handling are not reported. |
| Binary tissue labels | Supervise gene selection and classifier fitting | Label definitions, class counts, and the method for calculating gene–label association are not reported. Labels would not be needed as inputs when applying a fitted model to genuinely new cells. |
| Donor identity | Defines the biological grouping needed to assess transfer | There are 12 donors. The text does not identify donor ID as a classifier feature, and it does not use donor grouping for the split. |
| Neighborhood feature vector | Intermediate output | For each selected gene, the mean expression among cells in the radius. This block has 200 gene-level features. |
| Binary-label probability | Final model output | Logistic regression returns a probability for a binary class. A decision threshold and evidence of probability calibration are not reported. |

Source: M1, Results — Construction of the score, Table 1, and Methods. The exact dimension of the concatenated vector is not fully specified: it would be 400 if the own-expression block also contains only the same 200 genes, but the source does not explicitly state that restriction.

## Construction and evaluation, in their actual order

The **presentation order** is internal performance first (Table 2), followed by construction (Table 1), followed by Methods. That presentation should not be mistaken for the computational order. The reported workflow is:

1. **Select genes using the complete labeled dataset.** Among 60,000 cells from 12 donors, select the 200 genes most associated with the labels. This happens before the train/validation/test split. It is part of the reported method and is not silently replaced here with a cleaner procedure. [M1: Methods](input/prediction-method.md#methods)

2. **Randomly divide cells into three sets.** The training, validation, and test sets contain 42,000, 9,000, and 9,000 cells, respectively: 70%, 15%, and 15% of the total, recalculated from the reported counts. Every set contains cells from all 12 donors. Thus the test observations are held-out cells from already represented donors; there is no held-out donor group. The text does not explain exactly how the validation set is used. [M1: Methods](input/prediction-method.md#methods)

3. **Construct a fixed-radius neighborhood for each target cell.** Collect neighboring cells within 50 μm and average expression separately for each selected gene. This turns a variable number of neighbors into a fixed-size vector. As a mathematical explanation of the stated averaging operation, for gene \(g\) and a nonempty neighborhood \(N_i\):

   \[
   m_{ig}=\frac{1}{|N_i|}\sum_{j\in N_i}x_{jg}.
   \]

   Here \(x_{jg}\) is the supplied expression value for gene \(g\) in cell \(j\). The source does not specify whether the target cell counts as its own neighbor, how an empty neighborhood is handled, or whether neighbors can cross split boundaries. It also does not specify when neighborhood construction is performed relative to splitting. These details are unresolved, rather than assumed to follow a particular implementation. [M1: Results — Construction of the score; Table 1; Methods](input/prediction-method.md)

4. **Concatenate own expression and neighborhood means.** The neighborhood block provides a local summary alongside the target cell's own profile. The rationale is that local tissue context may carry label information beyond the expression of a single cell. The average discards the identities and arrangement of neighbors; it can also mix expression from different cell populations. These are implications of the stated operation, not additional results demonstrated by the source. [M1: Introduction; Results — Construction of the score](input/prediction-method.md)

5. **Fit an L2-regularized logistic classifier and predict probabilities.** Logistic regression combines the features into a linear predictor and maps it to a binary-class probability. L2 regularization penalizes coefficient magnitude; it does not by itself prevent label leakage or establish transfer across donors. The source names the classifier but does not report the regularization strength, fitting settings, preprocessing, or tuning procedure. Only parameter fitting on the training set is appropriate for a clean evaluation; the text does not provide implementation details sufficient to verify that step independently. [M1: Table 1; Methods](input/prediction-method.md)

6. **Compare internal discrimination with an expression-only baseline.** The comparator is logistic regression using cell expression, and both models use the same split. This makes the partition comparable, but equal tuning budgets and the precise baseline feature set are not specified. [M1: Results — Internal discrimination; Table 2; Methods](input/prediction-method.md)

## Assumptions that matter for use

The method assumes that a 50 μm neighborhood captures useful tissue context and that expression values can meaningfully be averaged across its cells. A fixed physical radius can yield different numbers and mixtures of neighbors in regions with different cell densities. A mean can therefore reflect local cell composition, donor or batch differences, or tissue structure. The source has not separated these possible explanations for the apparent improvement, and no radius sensitivity analysis is reported. [M1: Methods; Discussion](input/prediction-method.md)

Use on new material also requires compatible coordinate units, expression processing, gene measurements, and label definitions. These requirements follow from the inputs and classifier construction. Their stability across donors or institutions has not been tested in this material. The classifier's linear relation to log odds is another modeling choice: any performance advantage does not identify a biological interaction mechanism or establish that the selected genes cause the tissue label.

The neighborhood's availability at prediction time is particularly important. If a complete new specimen has unlabeled expression and coordinates for neighboring cells, computing its local averages may be a feasible application. If the intended task is to classify an isolated cell without neighborhood measurements, the required input is unavailable. If neighborhood features were constructed across train/test partitions, the evaluation would need to explain that information sharing and show how it matches deployment. The source does not establish whether that sharing occurred; the confirmed leakage is the use of all labels for gene selection.

## What the reported comparison supports

Table 2 reports AUROC 0.95 for neighborhood features plus logistic regression and 0.93 for expression-only logistic regression. The **absolute AUROC difference is 0.02**, recalculated as 0.95 − 0.93. AUROC concerns ranking the binary classes; it does not demonstrate calibrated probabilities, accuracy at a chosen threshold, or clinical benefit. No confidence interval, repeated-split estimate, or external test is supplied. [M1: Results — Internal discrimination; Table 2](input/prediction-method.md)

The description offers a candidate approach to improving internal cell-label discrimination. Two design choices limit interpretation. First, gene selection uses labels from the future validation and test sets, so the reported test result is not independent of feature selection and may be optimistic. Second, all 12 donors occur in every split, so the test does not directly evaluate the author's stated claim of generalization to new donors. Cells are nested within donors, and neighboring cells may also be spatially dependent; 9,000 test cells are not 9,000 independent donor validations. The size of any bias, and whether the relative advantage would survive a clean evaluation, cannot be quantified from these summaries. [M1: Methods; Results — Internal discrimination; Discussion](input/prediction-method.md)

### Compact evidence record

All rows use M1, the supplied fictional Markdown source linked above. Locations refer to its actual headings and tables; there are no PDF pages or figure panels.

| Claim or evaluation point | Source location | Comparison, sample, and unit | Action actually performed | Support boundary |
|---|---|---|---|---|
| Neighborhood means are concatenated with own expression and passed to L2 logistic regression | Results — Construction of the score; Table 1; Methods | Cell-level features; 50 μm radius; 200 selected genes | Read and cross-checked the text and Table 1 | Construction is described; exact own-expression dimension and neighborhood conventions remain unspecified. No implementation was run. |
| Split is 42,000/9,000/9,000 cells | Methods | 60,000 cells nested in 12 donors; every partition includes every donor | Read counts; recalculated their sum and fractions as 60,000 and 70%/15%/15% | The split holds out cells, not donors. Donor-specific cell counts and class counts are unavailable. |
| Reported test AUROC is higher with neighborhood features | Results — Internal discrimination; Table 2; Methods | Same split, 9,000 test cells; AUROC 0.95 versus 0.93 | Read both table entries; recalculated absolute difference as 0.02 | One internal comparison; no interval or external test. Tuning comparability is unresolved. |
| Feature-selection leakage affects the evaluation | Methods | Label-based gene selection on all 60,000 cells before partitioning | Checked the stated ordering of selection and splitting | Test labels influence the selected feature set. No corrected AUROC or bias estimate can be calculated from the text. |
| New-donor generalization is claimed but untested | Results — Internal discrimination; Methods; Discussion | Same 12 donors in all sets; no new-donor or new-institution validation | Compared the author claim against the split and Discussion | The author claim exceeds the evaluation's tested population. This does not show the method necessarily fails on new donors. |
| Added neighborhood information explains the improvement only provisionally | Introduction; Methods; Discussion | Expression-only baseline versus augmented features; fixed 50 μm radius | Read the comparator and stated absence of sensitivity analysis; checked the Discussion's unresolved attribution | No ablation isolates spatial arrangement, composition, or donor effects; no new algorithmic contribution is claimed. |

## How to judge whether it is useful for a new study

The feature construction is a practical design to adapt when spatial coordinates and expression are available for complete specimens. Its reported AUROC should not be treated as an expected performance estimate for new donors. The following are proposed validation steps arising from the source's specific gaps, not experiments already performed:

| Question to test | Proposed validation or control | Main criterion |
|---|---|---|
| Does the method transfer to unseen donors? | Partition donors before any supervised gene selection; select genes and tune preprocessing and regularization using training donors, then freeze the pipeline before testing held-out donors. Use donor-grouped internal validation, with independent new-donor or new-institution data when available. | AUROC and uncertainty that respect donor grouping; performance variation across held-out donors. Probability use additionally needs calibration assessment. |
| Does local spatial information contribute beyond the target cell's expression? | Compare expression-only, neighborhood-only, and concatenated models under the same preprocessing, features where applicable, partitions, and tuning budget. Add a within-donor shuffled-neighborhood control designed to disrupt local relationships while retaining donor membership. | Whether the augmented model's advantage is reproducible and reduced by disrupted neighborhoods. A positive result still would not establish a causal mechanism. |
| Is the neighborhood definition robust and reproducible? | Examine multiple prespecified radii; document self-inclusion, empty neighborhoods, specimen boundaries, coordinate units, and expression scaling. Choose any radius using training/validation data only. | Stability of discrimination and calibration, along with neighbor-count distributions and performance near tissue boundaries. |

These checks would determine whether the useful part is the local aggregation itself and whether its requirements fit the intended data. Repeated clean evaluation is needed before concluding that a small AUROC advantage is reliable.

## Source and reproducibility status

The supplied text states that its tables and descriptions are the complete available material and that no downloadable data or code exist. The expression matrix, coordinates, labels, fitted coefficients, and implementation were therefore not available for reanalysis. Software versions, hardware, runtime, and theoretical complexity are explicitly unreported; the name “lite” supplies no evidence about speed or resource use. [M1: Methods; Data and code](input/prediction-method.md)

All supplied source sections and both text tables were read. No images exist, so no visual check was applicable. Only the reported count totals, split fractions, and AUROC difference were recalculated. No model was fitted, uncertainty estimated, raw data analyzed, external literature retrieved, or official correction/retraction record checked. Official publication checks are not applicable to this explicitly fictional source.
