# Quick screening: donor-level association in PanelMap-SYN

**For your interest in the donor-level biological association, I would defer a full close reading.** The selected primary sections already show an uncertain estimate, an unadjusted age imbalance and no independent validation cohort. Their donor-level aggregation is a useful design feature to borrow, but the reported comparison provides limited support for a biological group difference. This is explicitly synthetic material, so none of its values should be cited as research findings.

M1 = [quick-screening.md](input/quick-screening.md), **screen-v1**, *PanelMap-SYN: an observational score with ancillary scanner calibration*. All persons, values and devices are fictional; no real journal, DOI or accession exists. Locations p1–p5 are the input's logical Markdown pages, not PDF or printed pages.

## The biological question and decisive result

The question is whether a fixed tissue score combining three stains differs between lesion and reference donors and warrants a larger, age-balanced study. There are **10 independent lesion donors and 10 independent reference donors**, each contributing one tissue and two technical scans. Scans are averaged within donor before comparison, giving **20 donor scores**, not 40 independent observations. The fixed score and acquisition procedure are reported as defined before this cohort. [M1 p1: Abstract and Introduction; p2: Results — Donor-level score; Table 1 — Donor comparison; Methods — Primary inference.](input/quick-screening.md)

Mean scores are **1.90 versus 1.50 units**. I recalculated lesion minus reference as **+0.40 units**, matching the reported estimate. The reported **95% CI [−0.30, 1.10] and two-sided P = 0.24** leave the direction and magnitude uncertain and do not establish a group difference. They also do not establish equivalence. The interval and P value were read as author reports; there are no donor scores or group variance summaries sufficient to reproduce them. [M1 p2: Results — Donor-level score; Table 1; Methods — Primary inference.](input/quick-screening.md)

The mean ages are **68 and 54 years**, a recalculated difference of **14 years**. No age-adjusted association is reported, so age imbalance remains a competing explanation for any score difference. I cannot determine the magnitude or direction of that potential confounding from group means alone. Convenience sampling at one visit and no longitudinal follow-up further limit generalisation and temporal interpretation. The source itself makes no treatment-benefit or causal mechanism claim. [M1 p1: Discussion; p2: Table 1; Methods — Primary inference.](input/quick-screening.md)

## Evidence record for the screening decision

| ID / claim or concern | Source version and location | Comparison, denominator and unit | Actual check | Support boundary |
|---|---|---|---|---|
| E1: correctly defined independent unit | M1 screen-v1, p1 Abstract; p2 Results/Table 1/Primary inference | 10 donors/group; 1 tissue/donor; 2 scans/tissue; within-donor averages | Read the sampling and aggregation descriptions; summed 20 donors, 20 tissues and 40 technical scans | Supports a donor-level comparison; technical scans do not add independent donors |
| E2: uncertain primary association | M1 p2 donor Results, Table 1 and Primary inference | Lesion vs reference donor means 1.90 vs 1.50; Welch comparison of 10 scores/group | Calculated 1.90 − 1.50 = 0.40; read CI [−0.30, 1.10], P = 0.24 | Interval crosses zero; neither established difference nor equivalence; interval/P not reproduced |
| E3: biological confounding | M1 p1 Discussion; p2 Table 1 and Primary inference | Mean ages 68 vs 54 years; no age adjustment | Calculated 68 − 54 = 14; read design limitations | Potential confounding unresolved; no individual-level adjustment or bias estimate available |
| E4: validation scope | M1 p1 Abstract/Introduction/Discussion; p2 donor Results | One donor cohort; ancillary calibration and drift described as technical experiments | Read these overview statements and the explicit absence of an independent cohort | No external biological replication; ancillary tables and protocols were left unread, so their engineering performance is not evaluated here |

## What merits follow-up

The transferable feature is **fixing a score and averaging technical repetitions before donor-level inference**. For a biological follow-up, test whether the lesion association persists in independently recruited, age-balanced donors, or use a prespecified adjustment strategy supported by individual-level data. Retain donors as the independent unit and report an effect estimate and interval. Define the smallest biologically meaningful difference before planning sample size; I did not perform a power calculation. These are proposed follow-up choices prompted by E2–E3.

A full reading becomes more useful if your purpose also includes transferring the acquisition procedure: the calibration, drift and engineering methods would then need direct inspection. For the stated biological association question, the authors' overview says those experiments add no independent donor cohort or disease outcome, and the primary donor uncertainty and confounding remain decisive. I have not independently verified the ancillary results.

## Exactly what I read and left unread

**Read:** source identity/version note; all of **p1** (Abstract, Introduction and Discussion); all of **p2** (Results — Donor-level score, complete Table 1 and Methods — Primary inference); and **p5 Data availability**. I scanned all section headings to locate relevant material. I checked Table 1 against the primary prose and performed only simple arithmetic on the reported means and counts. No images are supplied.

**Available but unread:** **p3** Results — Colour calibration and Table 2 — Artificial-strip calibration; **p4** Results — Scanner drift and Table 3 — Within-day drift summaries; **p5** Methods — Colour-strip correction and Methods — Drift protocol. Seeing their headings is not reading or verifying their results. The Discussion's description of calibration is an author report, not my inspection of those data.

**Unavailable / not performed:** The source says there are no extra files, images, supplement, donor-level vectors, patch-level readings or analysis scripts. Only this task's input directory was inventoried. I used no network source, downloaded nothing, did not inspect other trial outputs, and did not reproduce Welch inference, adjust for age or reanalyse raw data. [M1 p5: Data availability.](input/quick-screening.md)
