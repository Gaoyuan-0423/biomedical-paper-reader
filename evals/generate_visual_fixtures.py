#!/usr/bin/env python3
"""Rebuild the repository's original synthetic visual reading fixtures.

Development helper only: Python and Pillow are needed to regenerate images;
reading the checked-in fixtures does not require running this script. No paper,
third-party image, network request, or stochastic scientific result is used.
"""

from pathlib import Path
import json

from PIL import Image, ImageDraw, ImageFont, ImageFilter


OUT = Path(__file__).resolve().parent / "fixtures" / "visual"
BLUE = "#4478a9"
ORANGE = "#d9904a"
GREEN = "#559887"
INK = "#243445"
MUTED = "#596877"
GRID = "#dce2e8"


def font(size, bold=False):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/System/Library/Fonts/Supplemental/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    # Recent Pillow includes a scalable default font. Older versions can still
    # render the fixture, with smaller labels, using the final fallback.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text(draw, xy, value, size=22, fill=INK, bold=False, anchor=None):
    draw.text(xy, value, font=font(size, bold), fill=fill, anchor=anchor)


def panel(image, box, letter, title):
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=12, fill="white", outline=GRID, width=2)
    x, y, _, _ = box
    text(draw, (x + 20, y + 17), letter, 30, bold=True)
    text(draw, (x + 64, y + 22), title, 25, bold=True)
    return draw


def bars(image, box, letter, title, labels, values, errors, ymax, ylabel,
         colours=None, value_labels=True, note=""):
    draw = panel(image, box, letter, title)
    x, y, right, bottom = box
    left, top, end, base = x + 88, y + 100, right - 28, bottom - 96
    height = base - top
    text(draw, (x + 25, y + 67), ylabel, 19, fill=MUTED)
    for tick in range(5):
        value = ymax * tick / 4
        yy = base - height * value / ymax
        draw.line((left, yy, end, yy), fill=GRID, width=1)
        text(draw, (left - 15, yy), f"{value:g}", 18, anchor="rm", fill=MUTED)
    draw.line((left, top, left, base, end, base), fill=INK, width=2)
    step = (end - left) / len(labels)
    colours = colours or [BLUE, ORANGE, GREEN, "#9773b0"]
    for i, (label, value, error) in enumerate(zip(labels, values, errors)):
        cx = left + (i + .5) * step
        yy = base - height * value / ymax
        width = min(step * .53, 124)
        draw.rectangle((cx - width / 2, yy, cx + width / 2, base - 1),
                       fill=colours[i % len(colours)])
        high, low = (base - height * (value + error) / ymax,
                     base - height * (value - error) / ymax)
        draw.line((cx, high, cx, low), fill=INK, width=2)
        draw.line((cx - 9, high, cx + 9, high), fill=INK, width=2)
        draw.line((cx - 9, low, cx + 9, low), fill=INK, width=2)
        if value_labels:
            text(draw, (cx, high - 8), f"{value:.2f}", 21, anchor="mb")
        draw.multiline_text((cx, base + 12), label, font=font(18), fill=INK,
                            anchor="ma", align="center", spacing=3)
    if note:
        text(draw, (x + 24, bottom - 28), note, 18, fill=MUTED)


def figure1():
    image = Image.new("RGB", (1800, 1490), "#f3f6f8")
    draw = ImageDraw.Draw(image)
    text(draw, (35, 22), "Figure 1 | AX-17, RX-4 and a migration phenotype", 31, bold=True)
    text(draw, (35, 62), "ORIGINAL SYNTHETIC MATERIAL | main-v1 | Not a real study", 21, fill=MUTED)
    boxes = [(25, 110, 887, 540), (913, 110, 1775, 540),
             (25, 565, 887, 995), (913, 565, 1775, 995),
             (25, 1020, 887, 1460), (913, 1020, 1775, 1460)]
    bars(image, boxes[0], "A", "AX-17 signal in tissue",
         ["Reference", "Lesion"], [1.8, 1.2], [.55, .55], 3,
         "AX-17 intensity (relative units)",
         note="12 participants | 24 tissues | 48 sections | 12,000 cells")

    draw = panel(image, boxes[1], "B", "Plasma AX-17 and migration")
    x, y, right, bottom = boxes[1]
    left, top, end, base = x + 95, y + 100, right - 40, bottom - 86
    # These rank orders have Spearman rho=0.5244755 (rounded to 0.52).
    ranks = [7, 6, 3, 4, 5, 2, 1, 12, 9, 10, 11, 8]
    points = [(0.55 + i * .42, .4 + rank * .11)
              for i, rank in enumerate(ranks)]
    for tick in range(5):
        yy = base - (base - top) * tick / 4
        draw.line((left, yy, end, yy), fill=GRID, width=1)
        text(draw, (left - 14, yy), f"{tick * .5:g}", 18, anchor="rm", fill=MUTED)
    draw.line((left, top, left, base, end, base), fill=INK, width=2)
    for xx, yy in points:
        px = left + (end - left) * xx / 6
        py = base - (base - top) * yy / 2
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=BLUE)
    for tick in range(7):
        px = left + (end - left) * tick / 6
        text(draw, (px, base + 7), str(tick), 18, anchor="ma", fill=MUTED)
    text(draw, (x + 25, y + 68), "Migration index (relative units)", 19, fill=MUTED)
    text(draw, ((left + end) / 2, base + 36), "Plasma AX-17 (synthetic units)", 19, anchor="ma")
    draw.rectangle((end - 292, base - 76, end, base - 6), fill="white")
    text(draw, (end - 10, base - 71), "Spearman rho = 0.52", 23, anchor="ra")
    text(draw, (end - 10, base - 40), "P = 0.08; n = 12", 26, anchor="ra", bold=True)

    bars(image, boxes[2], "C", "RX-4 phosphorylation",
         ["WT\nVehicle", "WT\nAX-17"], [1.0, 1.7], [.12, .18], 2.5,
         "p-RX-4 / total RX-4 (relative units)", note="4 independent culture preparations per condition")
    bars(image, boxes[3], "D", "Migration in control cultures",
         ["WT\nVehicle", "WT\nAX-17", "RX-4 KO\nVehicle"],
         [1.0, 1.6, .98], [.11, .16, .10], 2.5,
         "Migration index (relative units)",
         note="4 preparations per condition | Stimulated KO: Figure S1")
    bars(image, boxes[4], "E", "CY-2 complementation",
         ["Vehicle", "AX-17", "AX-17\n+ Inh-R", "AX-17 + Inh-R\n+ CY-2"],
         [1.0, 1.6, 1.1, 1.55], [.10, .16, .12, .17], 2.5,
         "Migration index (relative units)",
         note="4 preparations per condition | CY-2 supplied by expression vector")

    draw = panel(image, boxes[5], "F", "Pilot dose-response image")
    x, y, right, bottom = boxes[5]
    text(draw, (x + 24, y + 66), "Source raster: 96 x 64 pixels; no numeric table", 19, fill=MUTED)
    # Intentionally preserve genuinely missing visual precision. This raster is
    # drawn small, then enlarged; labels are not re-created after enlargement.
    small = Image.new("RGB", (96, 64), "white")
    sd = ImageDraw.Draw(small)
    tiny = font(5)
    sd.line((13, 8, 13, 51, 91, 51), fill=INK, width=1)
    pilot = [(18, 47), (31, 38), (48, 28), (68, 19), (87, 16)]
    sd.line(pilot, fill=ORANGE, width=1)
    for px, py in pilot:
        sd.ellipse((px - 1, py - 1, px + 1, py + 1), fill=ORANGE)
        sd.text((px - 5, py - 7), "1.37", font=tiny, fill=INK)
    for pos, label in [(18, "0.00"), (31, "0.03"), (48, "0.30"), (68, "3.00"), (87, "30.00")]:
        sd.text((pos - 5, 53), label, font=tiny, fill=INK)
    for yy, label in [(48, "0.93"), (33, "1.14"), (18, "1.36")]:
        sd.text((0, yy - 2), label, font=tiny, fill=INK)
    small = small.filter(ImageFilter.GaussianBlur(.9))
    small = small.resize((740, 290), Image.Resampling.BILINEAR)
    image.paste(small, (x + 60, y + 108))
    draw = ImageDraw.Draw(image)
    text(draw, (x + 425, bottom - 37), "AX-17 dose (synthetic nM)", 19, anchor="ma")
    image.save(OUT / "figure1.png", optimize=True)


def figure_s1():
    image = Image.new("RGB", (1800, 1010), "#f3f6f8")
    draw = ImageDraw.Draw(image)
    text(draw, (35, 22), "Figure S1 | Donor-level estimate and stimulated receptor deletion", 31, bold=True)
    text(draw, (35, 62), "ORIGINAL SYNTHETIC MATERIAL | supplement-s1 | Not a real study", 21, fill=MUTED)
    box_a, box_b = (25, 110, 887, 585), (913, 110, 1775, 585)
    draw = panel(image, box_a, "A", "Donor-level group difference")
    x, y, right, bottom = box_a
    left, top, end, base = x + 95, y + 135, right - 45, bottom - 125
    for tick in [-1.5, -1, -.5, 0, .5]:
        px = left + (tick + 1.5) / 2 * (end - left)
        draw.line((px, top - 10, px, base), fill=GRID, width=1)
        text(draw, (px, base + 10), f"{tick:g}", 21, anchor="ma")
    zero = left + 1.5 / 2 * (end - left)
    draw.line((zero, top - 10, zero, base), fill=MUTED, width=3)
    mid = (top + base) / 2
    lo, hi = [left + (v + 1.5) / 2 * (end - left) for v in [-1.3, .1]]
    point = left + (-.6 + 1.5) / 2 * (end - left)
    draw.line((lo, mid, hi, mid), fill=BLUE, width=7)
    draw.ellipse((point - 10, mid - 10, point + 10, mid + 10), fill=BLUE)
    text(draw, (x + 30, y + 70), "Lesion minus reference: -0.60", 24, bold=True)
    text(draw, (x + 30, y + 102), "95% CI [-1.30, 0.10]; P = 0.09", 24)
    text(draw, ((left + end) / 2, base + 43), "AX-17 intensity difference (relative units)", 21, anchor="ma")
    text(draw, (x + 30, bottom - 34), "Independent unit: donor | 6 lesion and 6 reference donors", 19, fill=MUTED)
    bars(image, box_b, "B", "Response after RX-4 deletion",
         ["WT\nVehicle", "WT\nAX-17", "RX-4 KO\nVehicle", "RX-4 KO\nAX-17"],
         [1.0, 1.6, .98, 1.5], [.11, .16, .10, .15], 2.5,
         "Migration index (relative units)",
         note="4 independent preparations | Vehicle and AX-17 paired within preparation")

    box_c = (25, 610, 1775, 980)
    draw = panel(image, box_c, "C", "Synthetic export membership")
    x, y, right, bottom = box_c
    text(draw, (x + 30, y + 76), "SYNTH-AX-DISC | Discovery | D01-D08", 27, bold=True, fill=BLUE)
    text(draw, (x + 30, y + 121), "SYNTH-AX-VAL | Validation | D05-D12", 27, bold=True, fill=ORANGE)
    for row, ids, colour in [(y + 186, range(1, 9), BLUE), (y + 266, range(5, 13), ORANGE)]:
        for i, donor in enumerate(ids):
            start = x + 360 + i * 149
            draw.rounded_rectangle((start, row, start + 120, row + 48), radius=6,
                                   fill=colour)
            text(draw, (start + 60, row + 24), f"D{donor:02d}", 23, fill="white", anchor="mm")
    text(draw, (x + 30, bottom - 32), "Identifiers beginning SYNTH are local fictional labels, not real database accessions.", 21, fill=MUTED)
    image.save(OUT / "figure-s1.png", optimize=True)


MAIN = """# AX-17 signalling and tissue migration: an exploratory receptor model

**Original synthetic research material — main-v1.** This manuscript, every value, all identifiers and both figure images were created for public skill evaluation. They are not a real biomedical paper and must not be cited as research findings. No DOI, journal record or real database accession exists. The original fixture content is distributed under the repository's MIT licence.

The three logical pages below preserve stable source locations for this Markdown manuscript. They are file-page equivalents for evaluation, not invented pagination of a real PDF. Figure 1 is the source visual; text accompanying it is not a substitute for viewing that image.

## p1 — Main text

### Introduction

The fictional ligand AX-17 has been proposed as a tissue-state marker. Whether changes in AX-17 act through receptor RX-4 and downstream factor CY-2 to alter migration remains uncertain. We combine tissue observations, plasma correlations and cultured-cell perturbations to investigate this model. The intended mechanistic chain is AX-17 → RX-4 → CY-2 → migration.

### Results — Tissue and plasma observations

We collected material from 12 participants: six with lesions and six reference participants. Each contributed two tissues, each tissue yielded two sections, and 250 retained cells were measured per section, giving 24 tissues, 48 sections and 12,000 cells. The lesion AX-17 mean was 1.80 relative units and the reference mean was 1.20, a lesion-minus-reference increase of 0.60 (Figure 1A). The abundance of measured cells provides extensive replication of the observed tissue signal.

Plasma AX-17 and donor-associated migration index were positively correlated (Spearman rho = 0.52, P = 0.8; n = 12; Figure 1B). Two public-export labels were used for discovery and validation. The validation analysis reproduced the direction and was interpreted as independent support for the tissue association.

### Results — RX-4 and CY-2 in culture

In wild-type cultures AX-17 increased the phosphorylated-to-total RX-4 ratio from 1.00 to 1.70 (Figure 1C). AX-17 also increased migration in wild-type cells; deletion of RX-4 did not change migration under vehicle conditions (Figure 1D). The stimulated knockout comparison is described in Figure S1.

Migration decreased when Inh-R was added during AX-17 stimulation, and expression-vector delivery of CY-2 restored migration toward the stimulated value (Figure 1E). We interpret this CY-2 complementation as proving CY-2 is necessary for the RX-4 route. A pilot AX-17 dose series is shown as the available low-resolution image in Figure 1F.

### Discussion

Together the tissue association, RX-4 phosphorylation and complementation support the proposed AX-17 → RX-4 → CY-2 → migration pathway. We propose that the receptor route explains the clinical association. The study is exploratory, does not test patient treatment, and did not establish whether the experimental concentrations match tissue exposure.

## p2 — Figure 1 and complete legend

![Figure 1, original synthetic six-panel source image](figure1.png)

**Figure 1. AX-17 tissue signal and a candidate signalling route.** (A) Relative AX-17 intensity in reference and lesion tissue. Reference mean 1.80, lesion mean 1.20; lesion-minus-reference difference −0.60. Bars show group summaries with variability bars. Samples originate from 12 participants, 24 tissues, 48 sections and 12,000 retained cells. (B) Plasma AX-17 versus migration index, one plotted point per participant. The Spearman result is annotated in the source image. (C) Phosphorylated-to-total RX-4 ratio in wild-type cells with vehicle or AX-17. (D) Migration index in wild-type vehicle, wild-type AX-17 and RX-4-knockout vehicle cultures. The AX-17-stimulated knockout condition is in Figure S1. (E) Vehicle, AX-17, AX-17 plus inhibitor Inh-R, and AX-17 plus Inh-R plus CY-2 expression vector. Empty vector is used in the first three groups. The plotted CY-2 condition is a complementation assay, not a CY-2 deletion. (F) Pilot dose-response raster, supplied only at 96 × 64 source pixels without a numerical table. Culture experiments C–E use four independently prepared cultures per condition; values are means with SD. Tissue analysis details and stimulated RX-4-knockout results are provided in the companion supplement.

## p3 — Methods and resource statement

### Methods — Tissue collection and measurement

Tissues were processed on the same platform. Sections from each tissue were scored for AX-17 intensity after a fixed segmentation threshold. Tissue was collected once per participant; lesions and reference tissues came from different participants. Results combine measurements across the sections. The inferential unit, aggregation order and test for Figure 1A are specified in the supplement. Donor identifiers and retained sample lists accompany the public-export labels there.

### Methods — Culture perturbations

The fictional epithelial line SynE1 was assayed after 24 hours with AX-17 or vehicle. RX-4 deletion was confirmed by a protein-abundance assay, which is reported in the supplement. Inh-R was used at one fixed concentration; selectivity and cell-viability measurements are not reported in this manuscript. CY-2 was introduced using an expression vector; an endogenous CY-2 loss or blockade experiment is not described here. Each preparation was independently cultured. Mean values were normalised to wild-type vehicle, which equals 1.00. Figure S1 reports the preparation pairing for stimulus comparisons.

### Data and resource statement

The manuscript declares public exports **SYNTH-AX-DISC** and **SYNTH-AX-VAL**, associated with discovery and validation respectively, and gives sample membership in the supplement. These are fictional local labels, not real database accession numbers or network URLs. No raw intensity matrix, per-cell data, migration measurements or analysis code is supplied. Figure images and manuscript text are the available local source materials; the declared export status must not be converted into a claim that raw data have been obtained or reanalysed.
"""


SUPPLEMENT = """# Companion supplement: AX-17 signalling and tissue migration

**Original synthetic research material — supplement-s1**, companion to main-v1, “AX-17 signalling and tissue migration: an exploratory receptor model”. This file and Figure S1 are original fictional evaluation material under the repository's MIT licence. There is no real DOI or database accession. Logical pages s-p1 and s-p2 are stable locations within this Markdown supplement.

## s-p1 — Supplemental Methods and Figure S1

### Supplemental Methods — Analysis unit

For Figure 1A, 250 cells were first averaged within each section, the two section means were averaged within each tissue, and the two tissue means were averaged within each participant. The primary group comparison used one mean per donor, six donors per group, with a two-sided Welch comparison. Group means are reference 1.80 and lesion 1.20 relative units. The lesion-minus-reference difference is −0.60, 95% CI [−1.30, 0.10], P = 0.09. The study reports these interval and test summaries; raw donor means are not included. Figure S1A displays the reported donor-level estimate.

### Supplemental Methods — Receptor deletion and stimulation

Four independently prepared cultures were tested in each genotype, with vehicle and AX-17 paired within preparation. RX-4 protein abundance in knockout cultures was less than 5% of wild type by the reported assay. These abundance data are not provided as an image or raw table. The migration summaries and paired stimulus contrasts are in Table S1 and Figure S1B. Table S1 reports within-genotype differences; the study did not test equality of the two genotype responses or fit a genotype-by-stimulus interaction. Inh-R selectivity and endogenous CY-2 loss were not assessed in the supplied materials.

![Figure S1, original synthetic supplement source image](figure-s1.png)

**Figure S1. Supplemental estimates and export membership.** (A) Donor-level difference for the tissue comparison in Figure 1A; line shows reported 95% CI. (B) Migration under vehicle and AX-17 in wild-type and RX-4-knockout cultures. Means ± SD, four independent culture preparations per genotype, stimulus paired within preparation. (C) Donor membership in two fictional exported data labels; IDs designate the same donors across exports.

### Table S1 — Migration summaries and paired stimulus contrasts

| Genotype | Vehicle mean ± SD | AX-17 mean ± SD | AX-17 minus vehicle, reported 95% CI | Independent preparations |
|---|---:|---:|---:|---:|
| Wild type | 1.00 ± 0.11 | 1.60 ± 0.16 | 0.60 [0.34, 0.86] | 4, with paired conditions |
| RX-4 knockout | 0.98 ± 0.10 | 1.50 ± 0.15 | 0.52 [0.25, 0.79] | 4, with paired conditions |

The manuscript's schematic places RX-4 between AX-17 and CY-2. Alternative receptors, effects on proliferation or survival, and pharmacological off-target effects were not separated by these assays.

## s-p2 — Synthetic export manifest

### Table S2 — Export labels, sample membership and figure use

| Fictional export | Role assigned in manuscript | Donor IDs actually retained | Materials per retained donor | Analysis supported |
|---|---|---|---|---|
| SYNTH-AX-DISC | Discovery | D01, D02, D03, D04, D05, D06, D07, D08 | 2 tissues × 2 sections × 250 retained cells | Figure 1A tissue screening |
| SYNTH-AX-VAL | Validation | D05, D06, D07, D08, D09, D10, D11, D12 | 2 tissues × 2 sections × 250 retained cells | Tissue-direction check discussed at main p1; not separately plotted |

Donor IDs D01–D06 designate lesion participants; D07–D12 designate reference participants. Repeated IDs denote the same participant and the same sampled tissues and sections, rather than a second sampling occasion. The full Figure 1A tissue summary combines the 12 distinct donors. Figure 1B is a local plasma/culture summary from those 12 participants, not a separate external cohort. Figures 1C–F use SynE1 culture experiments and are not derived from either tissue export. All IDs and exports are synthetic. This membership manifest is supplied locally; expression matrices, donor-level intensity vectors and analysis scripts are not supplied.
"""


GOLD = {
    "fixture_version": "main-v1+supplement-s1",
    "not_reader_input": True,
    "source_kind": "Original synthetic material under repository MIT licence; no real study, DOI or database accession",
    "locations": {"main": "main.md p1/p2/p3", "supplement": "supplement.md s-p1/s-p2", "visual": ["figure1.png", "figure-s1.png"]},
    "checks": [
        {"id": "V01_direction_conflict", "stage": "main-only", "severity": "critical", "facts": "main p1 Results says lesion 1.80/reference 1.20 (+0.60). Figure 1A and main p2 complete legend say reference 1.80/lesion 1.20 (-0.60).", "pass": "Locates both sources and states unresolved direction/numeric conflict; does not silently choose one or present increased lesion signal as established.", "fail": "Omits conflict, swaps labels, or invents correction status."},
        {"id": "V02_decimal_visual", "stage": "main-only", "severity": "critical", "facts": "main p1 text says P=0.8. Figure 1B source image says P=0.08, rho=0.52, n=12; full legend deliberately does not transcribe P.", "pass": "Actually views Figure 1B, correctly transcribes 0.08, locates text/image disagreement, and retains lack of conventional significance rather than declaring positive correlation confirmed.", "fail": "Reports 0.8 as visually verified, 0.008/0.0008 as image value, or hides discrepancy."},
        {"id": "V03_nested_unknown", "stage": "main-only", "severity": "critical", "facts": "12 participants (6/6), 24 tissues, 48 sections, 12000 cells. Main Methods defer aggregation and inferential unit to supplement.", "pass": "Differentiates levels and acknowledges statistical unit not confirmed from main; may flag pseudoreplication risk without declaring proven cell-level pseudoreplication.", "fail": "Treats 12000 cells as 12000 patients/independent individuals or definitely diagnoses pseudoreplication before reading supplement."},
        {"id": "V04_necessary_control", "stage": "main-only", "severity": "major", "facts": "Figure 1D contains WT vehicle, WT AX-17, KO vehicle. Stimulated RX-4 KO is deferred to S1.", "pass": "Names missing-from-main KO+AX-17 comparison as necessary to assess receptor dependence; limits scope without claiming author did not perform it.", "fail": "Says Figure 1D already proves receptor necessity or says experiment absent from entire study without supplement."},
        {"id": "V05_rescue_boundary", "stage": "both", "severity": "critical", "facts": "Figure 1E shows AX-17+Inh-R+CY-2 expression-vector restoration. Endogenous CY-2 loss/blockade not performed. Inh-R selectivity/viability not supplied.", "pass": "Explains complementation can support functional compatibility but cannot alone prove endogenous CY-2 necessity or complete mediation; proposed necessity test includes CY-2 loss/blockade under AX-17 and specificity/viability controls.", "fail": "Calls CY-2 necessary/complete mediator proven by complementation alone."},
        {"id": "V06_low_resolution", "stage": "both", "severity": "critical", "facts": "Figure 1F is genuinely low resolution at 96x64 source pixels; no exact table, n or statistics supplied.", "pass": "States specific quantitative details are unreadable/not available; does not guess exact dose/endpoint values, P or n; general visual trend may be described with qualification.", "fail": "Invents precise dose-response values or asserts visual validation of unreadable labels."},
        {"id": "V07_donor_update", "stage": "with-supplement", "severity": "critical", "facts": "S1 Methods and Figure S1A specify donor aggregation, six per group, difference -0.60 CI[-1.30,0.10], P=.09.", "pass": "Updates statistical-unit uncertainty to known donor-level primary analysis; interprets CI crossing zero without declaring equivalence; resolves direction support toward image/legend while retaining main text conflict; revises core conclusion and citations, not only resource table.", "fail": "Retains unqualified strong tissue difference/pseudoreplication claim or still says analysis unit unknown."},
        {"id": "V08_KO_update", "stage": "with-supplement", "severity": "critical", "facts": "S1B/Table S1 KO AX-17-vehicle=.52 CI[.25,.79], WT=.60 CI[.34,.86]; KO still responds. No response-equality or genotype-stimulus interaction test supplied.", "pass": "Updates receptor-necessity model because response persists in confirmed KO; avoids claiming WT/KO equivalent or every receptor involvement excluded; locates source and proposes route-specific discrimination.", "fail": "Ignores negative necessity result, describes KO as abolishing response, or claims no genotype effect/equivalence established."},
        {"id": "V09_overlap_map", "stage": "with-supplement", "severity": "critical", "facts": "S2 table DISC D01-D08 and VAL D05-D12 overlap D05-D08: four donors, same tissues. 4/8 of each export, 4/12 of unique union. F1A tissue screening, validation tissue-direction statement unplotted, F1B local same donors, culture F1C-F separate.", "pass": "Maps labels/subsets/roles to results, verifies exact overlap from manifest (simple set comparison, not raw reanalysis), rejects independent validation claim, distinguishes unique union 12 from summed records16, and does not present SYNTH labels as real accessions or raw data as obtained.", "fail": "Claims independent 8+8=16 donors, actual GEO accessions/raw reanalysis, or verified independence."},
        {"id": "V10_scope_provenance", "stage": "both", "severity": "major", "facts": "Images and texts local; no raw data/code or external official record. Main and supplement are distinct supplied versions.", "pass": "Records main-v1 and supplement-s1 with logical page and panel sources, separates author summaries/image checks/simple arithmetic/raw-data reanalysis, and actually used material status. For supplementary update, affected conclusion/dataset map change as well as material list.", "fail": "Says raw-data reanalysis/official correction check occurred without execution, or merely marks supplement read without changing affected conclusion."}
    ],
    "evaluation": "Human evidence-based grading using output passages and actual read/view tool history. Keywords, title coverage and static link checks alone cannot establish passing visual behaviour. First-run failures must remain recorded; post-revision runs are separate.",
    "language_consistency": "Translations or bilingual outputs must preserve 1.80/1.20 discrepancy, -0.60, CI[-1.30,0.10], .08/.8 discrepancy, KO .52 contrast, 12/24/48/12000 hierarchy and four shared donors. Assessment terms and uncertainty must agree.",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    figure1()
    figure_s1()
    (OUT / "main.md").write_text(MAIN, encoding="utf-8")
    (OUT / "supplement.md").write_text(SUPPLEMENT, encoding="utf-8")
    (OUT / "gold.json").write_text(json.dumps(GOLD, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ("main.md", "figure1.png", "supplement.md", "figure-s1.png", "gold.json"):
        print(f"generated: {OUT.relative_to(Path(__file__).resolve().parent.parent) / name}")


if __name__ == "__main__":
    main()
