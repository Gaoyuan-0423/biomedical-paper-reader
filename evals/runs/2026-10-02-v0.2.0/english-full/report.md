# Marker A and tissue status: uncertainty at the donor level

The reported donor means are higher in cases, but the donor-level confidence interval includes zero, and the groups differ in age without adjustment. This fictional observational study therefore offers an uncertain association with tissue status; it does not establish that Marker A drives disease. Close reading is useful for understanding how the unit of analysis changes the interpretation of a result. If a project involves repeated tissues or many cells per donor, its donor-level comparison is a useful design feature to adapt.

## Source, scope, and research question

M1 is the supplied [Marker A in paired tissue observations](input/observational.md), an original fictional short article. Journal, authors, DOI, and official publication status are not applicable. The full available text was read: Introduction, both Results and Table 1, Methods, Discussion, and Data availability. There are no images or additional materials in the supplied input. The report is a full close reading of this available text, with locations given by its actual section and table titles. No external records were checked, no images were viewed, and no raw data or code were analyzed.

The Introduction asks whether Marker A levels differ between case and control tissues. It explicitly does not seek to estimate treatment benefit. Despite “paired tissue observations” in the title, the design describes repeated tissues within each donor; it does not describe matched case–control donor pairs. The Welch comparison is between two donor groups. These distinctions determine which conclusions the observations can support.

The argument follows the supplied order: a tissue-status question in the Introduction → a donor-level difference and a contrasting cell-level P value in Results → cohort details in Table 1 → the aggregation and sampling procedure in Methods → an associative interpretation followed by a stronger causal claim in Discussion. The latter claim is not supported by the preceding design.

## Results and methods read together

There are 18 independent donors, nine cases and nine controls. Each contributes four tissues, giving 36 tissues per group and 72 overall. The 18,000 cells are nested measurements; the number of cells per group or per donor is not provided. Methods first average cell scores within each donor, then compare donor means with a two-sided Welch t test and compute confidence intervals at the donor level. This aggregation keeps the primary comparison aligned with the independent donor units. It does not establish how tissue composition or unequal cell recovery affects each donor mean.

The case and control donor means are 2.1 and 1.4. Their reported difference is 0.7, with a 95% confidence interval of −0.1 to 1.5 and P = 0.083. The point estimate suggests higher scores in cases, but the interval is compatible with a small difference in the opposite direction as well as a larger positive difference. A nonsignificant result does not show equivalence or absence of association. No claim about a clinically meaningful magnitude is possible because the score scale and a relevant threshold are not defined. Locations: M1, “Results — A difference depends on the unit of analysis,” Table 1, and Methods.

The alternative test treats each cell as independent and reports P < 10⁻⁸. Because cells come from the same donors and tissues, this test counts nested measurements as independent observations and does not provide an appropriate substitute for donor-level uncertainty. The large measurement count is not evidence of a large independent cohort. This is a concrete problem with the described cell-level inference, rather than a criticism of collecting many cells.

Table 1 reports mean ages of 70 and 50 years, and Methods states that age was not adjusted. Thus disease group and age differ together; age is a plausible competing explanation for the score difference. Summary means alone cannot quantify its contribution or determine whether adjustment would reverse the result. Convenience sampling and collection at one time point also limit generalization and leave temporal order unresolved. No longitudinal follow-up, randomized treatment, or intervention is described. The causal sentence in Discussion consequently exceeds both the statistical and design evidence. The absence of intervention does not invalidate the study's original observational question.

## Core evidence record

All locations below refer to M1, the supplied fictional short article. Values are author-reported unless an actual recalculation is identified. No figure-based verification or raw-data reanalysis was performed.

| Claim, number, or criticism | Source and location | Comparison, denominator, and statistical unit | Actual check | Boundary or conflict |
|---|---|---|---|---|
| E1: 18 donors, 72 tissues, and 18,000 cells | Results; Table 1 | Nine donors and 36 tissues per group; four tissues per donor; cells per group unknown | Read the full text and table; recalculated 9 × 4 = 36 tissues per group | Tissues and cells do not expand the number of independent donors; no donor pairing is described |
| E2: donor mean difference 0.7; 95% CI −0.1 to 1.5; P = 0.083 | Results; Table 1; Methods | Cases versus controls; nine donor means per group; two-sided Welch test and donor-based CI | Recalculated 2.1 − 1.4 = 0.7 from reported means; read CI and P value | The interval includes zero; the test and interval were not reproduced from individual scores |
| E3: cell-level P < 10⁻⁸ cannot establish donor-level or causal evidence | Results; Methods; Discussion's final claim | 18,000 cells treated as independent, nested within 18 donors | Compared the stated cell-level assumption with the stated sampling hierarchy | The cell-level result conflicts with the strength of the Discussion claim; actual dependence cannot be estimated without data |
| E4: age is an unresolved competing explanation | Table 1; Methods | Mean age 70 versus 50 years; no adjustment | Recalculated a 20-year mean age difference; read the adjustment statement | Neither the direction nor size of confounding can be quantified from these summaries |
| E5: “drives disease” is unsupported by this design | Introduction; Methods; Discussion | Cross-sectional convenience sample; no intervention or temporal follow-up | Read the research aim and compare the causal statement with the design | Supports an observational question, not causal direction or treatment benefit |

## Limitations and research suggestions

The authors acknowledge the small sample and age imbalance. The main interpretive limitations identified here are the unresolved age difference, the inappropriate use of cell-level independence to strengthen a donor-level conclusion, and the absence of temporal or intervention evidence for the causal claim. Only Table 1 is available as data, so individual variability, donor balance in cell recovery, the reported statistical calculation, and adjusted estimates cannot be independently checked. These are limitations of this supplied study and its available records; the report does not invent unprovided effect sizes or bias estimates.

Two follow-up designs would directly address the observed gaps:

1. **Test whether the association persists when age is accounted for.** Recruit an independent donor cohort with overlap in age between case and control groups, using matching or prespecified stratification and an adjusted donor-level analysis. Define the score and tissue-sampling protocol in advance. The primary result should be an adjusted mean difference and confidence interval, accompanied by unadjusted and age-stratified estimates. This distinguishes a reproducible tissue-status association from one explained by the measured age imbalance. The number of donors requires variance information and a target precision; no power calculation has been performed here.
2. **Test robustness to the nested measurement structure.** Retain donor, tissue, cell-count, and cell-type identifiers. Compare the prespecified donor aggregate with an analysis that accounts for tissue and cell nesting, and check whether conclusions change after standardizing tissue or cell-type composition. Report donor-level uncertainty and sensitivity to the aggregation rule. Additional cells improve measurement detail, while independent donors remain necessary for replication. A causal interpretation would require a separate design capable of testing a defined causal mechanism.

The directly transferable element is aggregation to the independent sampling unit before drawing group-level conclusions. Matching, adjustment, and composition checks need adaptation to a new study's sampling and biological context. The follow-up hypotheses above are proposals from this reading, not findings from M1.

## Resources and citation use

| Resource | Availability and action |
|---|---|
| [M1 short article and Table 1](input/observational.md) | Supplied and fully read; table entries checked against the prose |
| Individual donor scores, raw data, and code | The Data availability section explicitly states that these are not public; none were analyzed |
| Images and supplementary materials | Not supplied; no image or supplement check was performed |
| Official version, corrections, and retractions | Not applicable to the fictional source; no external lookup performed |

For study design, revisit Methods alongside the donor- and cell-level comparisons in Results. For interpretation, revisit Table 1's age imbalance and the final Discussion sentence together. This material may be used as a fictional teaching example; it must not be cited as a real biomedical study.
