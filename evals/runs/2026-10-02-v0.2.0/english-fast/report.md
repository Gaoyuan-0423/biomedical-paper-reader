# Quick read: unresolved direction and mechanism in an AX-17 model

**Recommendation: selective close reading.** Figure 1 and its supporting methods merit close attention as an exercise in checking statistical and mechanistic claims. This is explicitly synthetic material, so it is not a source of biomedical findings to cite. A full close reading would be useful for auditing the proposed pathway only if its companion supplement becomes part of the task: the main manuscript alone leaves decisive controls and statistical details unavailable.

The intended question is whether AX-17 changes migration through RX-4 and CY-2. The provided cultures show increased RX-4 phosphorylation and migration after AX-17, inhibition-associated reduction in migration, and CY-2 complementation toward the stimulated value. These observations support a candidate route in SynE1 cultures. They do not establish the entire AX-17 → RX-4 → CY-2 → migration chain or an explanation of a clinical association. [M1, p1: Introduction; Results — RX-4 and CY-2 in culture; Discussion; p3: Methods — Culture perturbations.](input/main.md)

## What I actually read

M1 = [main.md](input/main.md), **main-v1**, original synthetic research material, titled *AX-17 signalling and tissue migration: an exploratory receptor model*. The source explicitly states that no real DOI, journal record or database accession exists. Locations p1–p3 are the source's logical Markdown pages, not PDF or printed page numbers.

I read the identity/version statement, **all of p1** (Introduction, both Results subsections and Discussion), the complete Figure 1 legend on p2, and **both supplied Methods subsections** on p3. I viewed the whole [Figure 1 image](input/figure1.png); group labels, numeric annotations and directions in A–E were legible. Panel F's dose and response values were unreadable in the supplied low resolution raster. This is broad coverage of a short manuscript; it should not be described as having read only an abstract or a few result sentences.

I did not read the p3 **Data and resource statement**. No companion supplement, Figure S1, raw data or analysis code was among the supplied input files. I did not search beyond this task's input directory, check external records, download anything, refit statistics or reanalyse raw data.

## Decisive evidence and risks

The tissue result has an unresolved direction conflict. The Results prose calls lesion AX-17 higher, whereas the complete legend and the visible bars show reference 1.80 and lesion 1.20 relative units. The latter give lesion minus reference = −0.60. Six lesion and six reference participants generate 24 tissues, 48 sections and 12,000 cells; the cells are nested measurements, not 12,000 independent participants. The inferential unit and aggregation order are deferred to the unavailable supplement. The source's claim of extensive replication therefore cannot be assessed from the cell count alone. [M1, p1: Results — Tissue and plasma observations; p2: Fig. 1A legend; p3: Methods — Tissue collection and measurement.](input/main.md)

The plasma relationship is described with rho = 0.52 and n = 12 in both text and image, but **P = 0.8 in the Results and P = 0.08 in Fig. 1B**. I retain this discrepancy; I did not calculate a replacement P value from plotted pixels. Neither value provides conventional evidence at a 0.05 threshold. Public-export labels called discovery and validation are not enough to establish independent participants; donor identifiers and sample lists are deferred to the supplement. [M1, p1: Results — Tissue and plasma observations; p2: Fig. 1B; p3: Methods — Tissue collection and measurement.](input/main.md)

In culture, RX-4 phosphorylation rises from 1.00 to 1.70; wild-type migration rises from 1.00 to 1.60. RX-4 knockout vehicle migration is 0.98, but this baseline comparison does not test whether RX-4 is required for the **AX-17-induced** response. The stimulated knockout comparison is Figure S1, which was not supplied. In Fig. 1E, migration is 1.00 with vehicle, 1.60 with AX-17, 1.10 with AX-17 plus Inh-R, and 1.55 after adding CY-2 expression. This complementation is consistent with functional restoration under inhibitor treatment. An endogenous CY-2 loss or blockade is not described in the supplied Methods, so complementation alone cannot prove CY-2 necessity. Inh-R selectivity and viability measurements are not reported in the main manuscript; their wider availability was not checked. [M1, p1: Results — RX-4 and CY-2 in culture; p2: Fig. 1C–E and complete legend; p3: Methods — Culture perturbations.](input/main.md)

## Compact evidence record

| ID / claim or concern | Source version and location | Comparison, sample and unit | Actual check | Support boundary or conflict |
|---|---|---|---|---|
| E1: tissue direction and replication | M1 p1 tissue Results; p2 Fig. 1A; p3 tissue Methods | Lesion vs reference; 6 participants/group; 2 tissues/participant, 2 sections/tissue, 250 cells/section | Read text/legend/methods; visually checked A; calculated 1.20 − 1.80 = −0.60 | Prose reverses image/legend direction; inferential unit and dependence handling cannot be checked without supplement |
| E2: plasma correlation | M1 p1 tissue/plasma Results; p2 Fig. 1B | One point/participant; n = 12; rho = 0.52 | Read and visually checked the statistic annotation | P = 0.8 in text vs 0.08 in image; no raw-data recomputation; independence of labelled validation unverified |
| E3: RX-4 response and migration | M1 p1 culture Results; p2 Fig. 1C–D and legend; p3 culture Methods | WT vehicle vs AX-17; KO vehicle only; four independent culture preparations/condition; mean ± SD | Read relevant sources; checked visible group/value labels: phosphorylation 1.00 → 1.70; migration 1.00 → 1.60; KO vehicle 0.98 | Activation and phenotype change in this line; stimulated KO absent from supplied materials, so receptor requirement unresolved |
| E4: CY-2 complementation | M1 p1 culture Results; p2 Fig. 1E and legend; p3 culture Methods | Vehicle / AX-17 / AX-17+Inh-R / AX-17+Inh-R+CY-2; four preparations/condition; mean ± SD | Checked 1.00 / 1.60 / 1.10 / 1.55 and expression-vector design; calculated 1.55 − 1.10 = 0.45 | Supports restoration under this condition; does not prove endogenous CY-2 necessity; inhibitor specificity/viability unverified |
| E5: dose response | M1 p2 Fig. 1F and legend | Pilot raster; 96 × 64 source pixels; no numerical table | Viewed F within whole image | Numerical dose/response relationship cannot be read reliably; no EC50 or precise dose claim |

## Priority for a close read

First resolve Fig. 1A's direction and its participant-level inference, and Fig. 1B's statistic using the authoritative analysis output. Next inspect stimulated wild-type versus RX-4 knockout cultures and their preparation pairing in Figure S1. Finally test the proposed CY-2 necessity with a loss or blockade condition during AX-17 exposure, a matched control and restoration resistant to that perturbation; compare the AX-17-induced migration change while checking viability and RX-4 phosphorylation. These are proposed validation priorities, not experiments shown here.

If studying migration mechanisms, the useful design feature is the sequence from observational measurements to defined perturbations and complementation. It needs adaptation to the relevant model and exposures; the source itself does not establish patient treatment effects or matching of culture concentration to tissue exposure.

![Figure 1: supplied synthetic source image, main-v1](input/figure1.png)

Only the supplied manuscript and figure were accessed. The unread resource statement and unavailable supplement cannot serve as confirmed data or replication entry points.
