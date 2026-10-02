# PanelMap-SYN: an observational score with ancillary scanner calibration

Original synthetic research material, screen-v1. All persons, values and devices are fictional; no journal, DOI or real database accession exists. Logical pages below identify sections of this Markdown input, not PDF pages. This complete supplied manuscript has no images, supplement or raw data.

## p1 — Abstract, research question and discussion

### Abstract

We ask whether a tissue score differs between lesion and reference donors. The prespecified primary analysis compares donor mean scores, not individual cells. Twenty independent donors supplied paired technical scans of one tissue per donor. The lesion-minus-reference estimate is 0.40 units, with 95% CI [−0.30, 1.10] and P = 0.24. Two ancillary technical experiments assess colour-strip calibration and scanner drift. Neither experiment adds an independent donor cohort or a disease outcome. This is an exploratory association study, not a causal or treatment study.

### Introduction

PanelMap-SYN combines three measured stains into a fixed tissue score. We investigate whether the score can motivate a larger, age-balanced donor study. Scanner engineering is an ancillary purpose, relevant to labs transferring the acquisition procedure. The main donor comparison is Table 1; colour calibration and drift are Tables 2 and 3.

### Discussion

The primary estimate is uncertain and does not establish a donor-group difference or equivalence. Mean donor ages differ by 14 years, and no adjusted association is reported. Calibration is repeatable on artificial strips, but this does not resolve biological confounding. Technical repetition improves measurement precision without adding independent participants. No claim of patient treatment benefit or causal disease mechanism is made. For biological follow-up, prioritize independent donors and confounding control; engineering follow-up would require its own drift and calibration assessment.

## p2 — Primary donor comparison

### Results — Donor-level score

Ten lesion donors and ten reference donors each supplied one tissue. Two scans per tissue were averaged before one score per donor entered the primary comparison. Mean scores were 1.90 and 1.50 units. The lesion-minus-reference difference was 0.40, with reported 95% CI [−0.30, 1.10] and two-sided P = 0.24. Age means were 68 and 54 years. No independent validation cohort was collected.

### Table 1 — Donor comparison

| Quantity | Lesion | Reference |
|---|---:|---:|
| Independent donors | 10 | 10 |
| Tissues | 10 | 10 |
| Technical scans | 20 | 20 |
| Mean score | 1.90 | 1.50 |
| Mean age, years | 68 | 54 |

### Methods — Primary inference

Convenience sampling at one visit, with no random assignment or longitudinal follow-up. Technical scan pairs share the same tissue and donor. Scores are averaged within each donor before a Welch comparison of the 10 donor scores per group; intervals use donor-level uncertainty. Lesion-minus-reference direction is used throughout. The score weights and acquisition protocol were fixed before measuring this cohort. No age adjustment, treatment comparison or causal intervention was performed. Raw donor scores are unavailable, so the reported interval and P value cannot be reproduced from Table 1 alone.

## p3 — Ancillary colour-strip results

### Results — Colour calibration

Eight artificial strips were each scanned on two devices with repeated passes. A strip contains three printed reference patches rather than biological tissue. Comparing fixed patch ratios before and after an engineering correction evaluates whether the correction stabilizes the acquisition scale. The technical difference is not an effect estimate for the donor comparison in Table 1. The correction was not chosen from donor labels.

### Table 2 — Artificial-strip calibration

| Printed patch | Device A mean ratio | Device B mean ratio | Difference B−A |
|---|---:|---:|---:|
| Cyan | 1.00 | 1.04 | 0.04 |
| Magenta | 1.00 | 0.97 | −0.03 |
| Yellow | 1.00 | 1.02 | 0.02 |

## p4 — Ancillary drift results

### Results — Scanner drift

The same artificial reference plate was measured at six times during one day on each of two devices. Maximum fractional changes from the first pass were 0.06 and 0.08. There are two devices, one plate, and twelve timed plate readings; timed readings are not twelve independent devices. These summaries do not characterize between-day drift, patient cohorts, tissue degradation or a biological mechanism. No formal test of device equivalence is supplied.

### Table 3 — Within-day drift summaries

| Device | Times | Maximum fractional change |
|---|---:|---:|
| A | 6 | 0.06 |
| B | 6 | 0.08 |

## p5 — Ancillary engineering methods and availability

### Methods — Colour-strip correction

Eight printed strips were mounted in a fixed holder. Devices A and B used the same fixed illumination and reference patch layout. The acquisition order alternated devices, while operators repeated passes without replacing the strips. A fixed ratio correction was applied to each colour channel using the printed reference, not learned from biological labels. Repeated passes estimate within-strip measurement variation. Assessing independent transfer would require new strips, additional devices and another day; none were included. Holder alignment, illumination stability and patch aging remain possible technical effects. The manuscript reports only the summarized patch ratios; no pass-level intensities are supplied.

### Methods — Drift protocol

One reference plate remained mounted through six timed readings per device. Each timed reading began with the same warm-up interval and used a fixed gain. The first reading is the reference for the reported fractional change. No plate replacement, fresh-day acquisition, temperature variation or randomized gain condition was studied. Hence drift is a within-day technical description for these devices and this plate. The drift maximum is a descriptive extremum, not a confidence interval or a test statistic. Repeated timed reads cannot substitute for device-level replication when transferring the scanner procedure.

### Data availability

Only Tables 1–3 and this complete text are supplied. There are no images, extra files, donor-level vectors, patch-level readings or analysis scripts. No network source is available for this fictional study. Technical data and biological observations have different sample units; neither can be described as raw-data reanalysis when only the tables are read.
