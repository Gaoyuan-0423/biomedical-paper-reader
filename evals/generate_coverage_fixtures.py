#!/usr/bin/env python3
"""Rebuild the original all-at-once coverage fixture (development only).

Pillow is needed to regenerate PNGs, not to read the checked-in fixture. No
network, manuscript pixels, stochastic data, or third-party scientific results
are used. For a blind run, supply only main.md, supplement.md and figure*.png;
keep this generator and gold.json outside the reader's input directory.
"""

from pathlib import Path
import json
import math

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).resolve().parent / "fixtures" / "coverage"
INK, MUTED, GRID = "#253444", "#526373", "#d7e0e7"
BLUE, RED, GREEN, PURPLE = "#2c72b8", "#c53f43", "#3b9279", "#9263ba"
W, H = 1600, 1000


def font(size=23, bold=False):
    names = (["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
             ["/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text(d, xy, s, size=23, fill=INK, bold=False, anchor=None):
    d.text(xy, str(s), fill=fill, font=font(size, bold), anchor=anchor)


def canvas(title):
    im = Image.new("RGB", (W, H), "#f4f7fa")
    d = ImageDraw.Draw(im)
    text(d, (35, 20), title, 32, bold=True)
    text(d, (35, 63), "ORIGINAL SYNTHETIC MATERIAL | coverage-v1 | Not a real study", 21, fill=MUTED)
    return im


def panel(im, box, label, title):
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(box, 12, fill="white", outline=GRID, width=2)
    x, y, _, _ = box
    text(d, (x + 18, y + 16), label, 28, bold=True)
    text(d, (x + 62, y + 19), title, 24, bold=True)
    return d


def lines(d, xy, rows, size=24, gap=38):
    for i, row in enumerate(rows):
        text(d, (xy[0], xy[1] + i * gap), row, size)


def bars(im, box, label, title, labels, values, ymax, unit, colours=None):
    d = panel(im, box, label, title)
    x, y, r, b = box
    left, top, end, base = x + 80, y + 100, r - 32, b - 95
    text(d, (x + 24, y + 62), unit, 20, fill=MUTED)
    for t in range(5):
        val = ymax * t / 4
        yy = base - (base - top) * val / ymax
        d.line((left, yy, end, yy), fill=GRID)
        text(d, (left - 12, yy), f"{val:g}", 18, anchor="rm")
    d.line((left, top, left, base, end, base), fill=INK, width=2)
    step = (end - left) / len(labels)
    colours = colours or [BLUE, GREEN, RED, PURPLE]
    for i, (name, val) in enumerate(zip(labels, values)):
        cx, yy = left + (i + .5) * step, base - (base - top) * val / ymax
        d.rectangle((cx - step * .26, yy, cx + step * .26, base - 1), fill=colours[i % len(colours)])
        text(d, (cx, yy - 8), f"{val:g}", 22, anchor="mb")
        text(d, (cx, base + 14), name, 20, anchor="ma")


def heatmap(im, box, label, title, rows, cols, values, note="Relative activity (0-1)"):
    d = panel(im, box, label, title)
    x, y, r, b = box
    left, top, end, base = x + 205, y + 108, r - 35, b - 100
    cw, ch = (end - left) / len(cols), (base - top) / len(rows)
    for i, row in enumerate(rows):
        text(d, (left - 15, top + (i + .5) * ch), row, 21, anchor="rm")
        for j, val in enumerate(values[i]):
            rgb = (int(242 - 150 * val), int(246 - 90 * val), int(250 - 140 * val))
            bounds = (left + j * cw, top + i * ch, left + (j + 1) * cw, top + (i + 1) * ch)
            d.rectangle(bounds, fill=rgb, outline="white", width=2)
            text(d, (left + (j + .5) * cw, top + (i + .5) * ch), f"{val:.2f}", 21, anchor="mm")
    for j, col in enumerate(cols):
        text(d, (left + (j + .5) * cw, base + 15), col, 20, anchor="ma")
    text(d, (x + 24, b - 36), note, 20, fill=MUTED)


def save(im, name):
    im.save(OUT / name, optimize=True)


def figure1():
    im = canvas("Figure 1 | Cohort, B cell abundance and site-associated signaling")
    bars(im, (25, 110, 785, 550), "A", "55 tissue samples / 20 patients",
         ["HC", "PRI", "LM", "PM"], [10, 20, 10, 15], 24, "Tissue samples (n)")
    bars(im, (815, 110, 1575, 550), "B", "TAAB fraction within B cells",
         ["HC", "PRI", "LM", "PM"], [.08, .12, .16, .10], .20, "Patient-aggregated median fraction")
    d = panel(im, (25, 580, 1575, 970), "C", "GSEA: malignant epithelial cells, LM versus PM")
    lines(d, (60, 660), ["TNF-alpha signaling via NF-kB: NES = 3.20; nominal P = 0.001",
                           "Ranking: patient-aggregated malignant epithelial pseudobulk, LM - PM",
                           "Eligible patients: LM n = 10, PM n = 15; paired component modeled",
                           "Gene set: SYN_HALLMARK_TNFA_NFKB (fixed 30-gene set)",
                           "Observational comparison; no genetic or cytokine perturbation"])
    save(im, "figure1.png")


def figure_s1():
    im = canvas("Figure S1 | Quality control and patient integration")
    bars(im, (25, 110, 785, 620), "A", "QC by tissue site",
         ["HC", "PRI", "LM", "PM"], [1800, 2200, 2050, 2150], 3000, "Median detected genes per retained cell")
    d = panel(im, (815, 110, 1575, 620), "B", "UMAP after batch correction")
    left, top = 850, 205
    for p in range(20):
        angle = p * math.tau / 20
        colour = (60 + p * 7, 120 + p * 3, 200 - p * 4)
        for k in range(4):
            xx = left + 320 + 190 * math.cos(angle) + k * 8
            yy = top + 150 + 110 * math.sin(angle) + (k % 2) * 8
            d.ellipse((xx - 7, yy - 7, xx + 7, yy + 7), fill=colour)
        text(d, (left + 320 + 250 * math.cos(angle), top + 150 + 165 * math.sin(angle)),
             f"P{p + 1:02d}", 16, anchor="mm")
    d = panel(im, (25, 650, 1575, 970), "C", "Retained cells and batch provenance")
    lines(d, (60, 730), ["8,000 retained cells: HC 1,000; PRI 2,500; LM 2,000; PM 2,500",
                           "55 tissue libraries nested within P01-P20; correction covariate = library",
                           "All 20 patients remain represented after filtering; no patient exclusion",
                           "Patient-label display alone does not validate biological integration"])
    save(im, "figure-s1.png")


def figure_s2():
    im = canvas("Figure S2 | Patient-level coexpression modules")
    heatmap(im, (25, 110, 1575, 610), "A", "Module eigengene correlations",
            ["turquoise", "brown", "blue"], ["TNF score", "TAAB fraction", "EMT score"],
            [[.62, .48, .31], [.18, .24, .12], [.20, .15, .35]], "Displayed positive correlations (r); 20 patient aggregates")
    d = panel(im, (25, 650, 1575, 970), "B", "Turquoise module overview")
    lines(d, (60, 730), ["Turquoise module: 30 genes; brown: 24 genes; blue: 18 genes",
                           "Module-network input = patient-aggregated normalized expression",
                           "Turquoise r with TNF score = 0.62; exploratory association",
                           "Functional overrepresentation results are shown separately in Figure S8"])
    save(im, "figure-s2.png")


def figure_s3():
    im = canvas("Figure S3 | CD86 communication network")
    d = panel(im, (25, 110, 785, 970), "A", "CD86 inferred signaling network")
    nodes = {"TAAB": (300, 320), "DC": (590, 320), "CD4 T": (300, 650), "CD8 T": (590, 650)}
    edges = [("TAAB", "CD4 T", .30, (318, 485)),
             ("TAAB", "CD8 T", .12, (380, 402)),
             ("DC", "CD4 T", .22, (465, 570)),
             ("DC", "CD8 T", .08, (610, 485))]
    for source, target, val, label_xy in edges:
        sx, sy = nodes[source]
        tx, ty = nodes[target]
        d.line((sx, sy, tx, ty), fill=GREEN, width=round(val * 35))
        length = math.hypot(tx - sx, ty - sy)
        ux, uy = (tx - sx) / length, (ty - sy) / length
        tipx, tipy = tx - ux * 47, ty - uy * 47
        d.polygon([(tipx, tipy), (tipx - ux * 18 - uy * 9, tipy - uy * 18 + ux * 9),
                   (tipx - ux * 18 + uy * 9, tipy - uy * 18 - ux * 9)], fill=GREEN)
        text(d, label_xy, f"{val:.2f}", 24)
    for name, (xx, yy) in nodes.items():
        d.ellipse((xx - 45, yy - 45, xx + 45, yy + 45), fill=BLUE)
        text(d, (xx, yy + 62), name, 24, anchor="mm")
    text(d, (60, 880), "Edges: source CD86 -> target CD28", 23)
    heatmap(im, (815, 110, 1575, 610), "B", "Communication probability",
            ["TAAB ->", "DC ->"], ["CD4 T", "CD8 T"], [[.30, .12], [.22, .08]], "Model probabilities; not counts of physical contacts")
    d = panel(im, (815, 650, 1575, 970), "C", "Scope")
    lines(d, (850, 730), ["Expression-derived candidate network",
                            "Receptor availability: CD28 on T cells",
                            "No intervention or spatial validation",
                            "Patient/library hierarchy retained"])
    save(im, "figure-s3.png")


def figure_s4():
    im = canvas("Figure S4 | Copy number inference and epithelial identity")
    heatmap(im, (25, 110, 785, 610), "A", "CNV score by chromosome block",
            ["Normal epi", "Tumor epi", "B reference"], ["Chr1-4", "Chr5-8", "Chr9-12"],
            [[.05, .06, .04], [.76, .64, .81], [.02, .03, .02]], "CNV amplitude, normalized within this synthetic example")
    d = panel(im, (815, 110, 1575, 610), "B", "UMAP by CNV-derived status")
    for i in range(100):
        for xx, yy, colour in [(1030 + (i % 10) * 12, 280 + (i // 10) * 12, BLUE),
                               (1320 + (i % 10) * 12, 390 + (i // 10) * 12, RED)]:
            d.ellipse((xx, yy, xx + 6, yy + 6), fill=colour)
    text(d, (860, 540), "Blue: normal    Red: tumor", 25)
    d = panel(im, (25, 650, 1575, 970), "C", "Gene cluster summary")
    lines(d, (60, 730), ["C1-C3: epithelial structure; C4-C6: stress response; C7-C9: immune activation",
                           "Nine clusters partition 90 genes (10 per cluster); this is not a cell proportion plot",
                           "Normal/tumor labels derive from CNV inference using B cells as diploid reference",
                           "CNV-derived status is inferred; direct DNA copy number was not measured"])
    save(im, "figure-s4.png")


def figure_s5():
    im = canvas("Figure S5 | Tissue-site communication differences")
    heatmap(im, (25, 110, 1575, 610), "A", "MIF-CD74-CXCR4 model probabilities",
            ["Epi -> TAAB", "TAAB -> Epi", "DC -> CD4 T"], ["PRI", "LM", "PM"],
            [[.18, .26, .22], [.07, .09, .08], [.12, .15, .14]], "Communication probability; expression-derived, not ligand secretion measured")
    d = panel(im, (25, 650, 1575, 970), "B", "Comparison design")
    lines(d, (60, 730), ["PRI = primary tumor; LM = liver metastasis; PM = peritoneal metastasis",
                           "Rows encode direction explicitly: left label is source -> target",
                           "Epithelial cells have higher modeled MIF output to TAAB than reverse output",
                           "Differences are descriptive; no functional necessity test was performed"])
    save(im, "figure-s5.png")


def figure_s6():
    im = canvas("Figure S6 | B cell potency and relative differentiation order")
    for box, label, title, entries, colours in [
        ((25, 110, 785, 520), "C", "Potency score", ["1.0: Totipotent", "0.5: Multipotent", "0.0: Differentiated"], [RED, GREEN, BLUE]),
        ((815, 110, 1575, 520), "D", "Relative order", ["1.0: Less diff.", "0.5: Intermediate", "0.0: More diff."], ["#e7d64f", PURPLE, "#191523"]),
    ]:
        d = panel(im, box, label, title)
        for i, (entry, colour) in enumerate(zip(entries, colours)):
            xx, yy = box[0] + 45, box[1] + 105 + i * 82
            d.rectangle((xx, yy, xx + 85, yy + 48), fill=colour)
            text(d, (xx + 115, yy + 24), entry, 28, anchor="lm")
    bars(im, (25, 550, 1575, 970), "F", "Developmental potential by phenotype",
         ["TAAB", "Early B", "Memory B", "Naive B", "Plasma B", "Reg B"],
         [.55, .13, .10, .08, .09, .07], 1, "Median potency score (0-1)", [PURPLE, BLUE, GREEN])
    save(im, "figure-s6.png")


def figure_s7():
    im = canvas("Figure S7 | Site-specific GSVA, GSEA and ligand-receptor expression")
    d = panel(im, (25, 110, 785, 460), "A", "GSVA: LM minus PM")
    text(d, (60, 180), "Blue = High in PM", 25, fill=BLUE, bold=True)
    text(d, (425, 180), "Red = High in LM", 25, fill=RED, bold=True)
    d.line((400, 225, 400, 405), fill=INK, width=2)
    text(d, (65, 246), "TNFA_NFKB", 21)
    d.rectangle((401, 250, 670, 285), fill=RED)
    text(d, (680, 267), "+4.5", 21, anchor="lm")
    text(d, (65, 330), "OXPHOS", 21)
    d.rectangle((185, 332, 399, 367), fill=BLUE)
    text(d, (290, 349), "-3.8", 21, fill="white", anchor="mm")
    text(d, (60, 415), "x = t value of GSVA score", 21, fill=MUTED)
    d = panel(im, (815, 110, 1575, 460), "D", "GSEA: high-EMT epi, LM versus PRI")
    lines(d, (850, 182), ["TNF-alpha signaling via NF-kB",
                            "NES = 1.65; nominal P = 0.04",
                            "Ranking: high-EMT epithelial pseudobulk",
                            "LM - PRI; patients 10 vs 20",
                            "Set: SYN_HALLMARK_TNFA_NFKB"], size=23, gap=45)
    d = panel(im, (25, 490, 1575, 970), "F", "MIF and receptor expression across cell populations")
    cols = ["TAAB", "Low_EMT", "High_EMT", "PM epi", "LM epi", "memory T", "CD8 T", "Treg"]
    rows = ["CD74", "CXCR4", "MIF"]
    values = [[2, .4, .3, .3, .2, .2, .3, .3], [.8, .2, .2, .2, .2, .7, .9, .8], [.3, 1.7, 1.4, 1.8, 1.6, .2, .1, .2]]
    percentages = [[90, 55, 50, 55, 50, 40, 45, 45], [80, 40, 40, 40, 40, 75, 85, 80], [65, 85, 80, 90, 85, 55, 50, 50]]
    left, top, step = 235, 635, 151
    for j, name in enumerate(cols):
        text(d, (left + j * step, 577), name, 21, anchor="mm")
    for i, name in enumerate(rows):
        yy = top + i * 85
        text(d, (70, yy), name, 27, bold=True)
        for j, value in enumerate(values[i]):
            xx, radius = left + j * step, 9 + percentages[i][j] * .13
            frac = value / 2
            colour = (int(250 - 60 * frac), int(218 - 190 * frac), int(180 - 145 * frac))
            d.ellipse((xx - radius, yy - radius, xx + radius, yy + radius), fill=colour, outline="#ad764d")
            text(d, (xx, yy + 30), f"{value:.1f} / {percentages[i][j]}%", 18, anchor="ma")
    text(d, (60, 924), "Color = mean expression (0 light, 2 red); size = detected cells (%); numeric labels = expression / %", 20)
    save(im, "figure-s7.png")


def figure_s8():
    im = canvas("Figure S8 | KEGG overrepresentation of the turquoise module")
    bars(im, (25, 110, 1575, 680), "A", "Turquoise module: 30 genes",
         ["TNF signaling", "NF-kB signaling", "Antigen processing", "Cell adhesion"],
         [4.3, 3.5, 2.5, 1.8], 5, "-log10(BH-adjusted P)")
    d = panel(im, (25, 715, 1575, 970), "B", "Overrepresentation inputs")
    lines(d, (60, 795), ["Module genes = 30; background = 1,000 tested expressed genes",
                           "Synthetic pathway membership table; BH correction over 12 tested pathways",
                           "Overrepresentation describes gene membership, not measured pathway activation"])
    save(im, "figure-s8.png")


MAIN = """# B cell states and site-associated signaling in gastric tissue

Original synthetic local reading material, coverage-v1. This is a fictional observational study; no real patient records, DOI, accession, raw counts or executable scientific analysis are supplied. Figure graphics were drawn for this material.

## p1: Abstract and methods

We profiled 55 tissue samples from 20 patients. Sites are healthy adjacent tissue (HC), primary gastric tumor (PRI), liver metastasis (LM), and peritoneal metastasis (PM). P01-P10 contributed HC, PRI, LM and PM (40 tissues); P11-P15 contributed PRI and PM (10 tissues); P16-P20 contributed PRI only (5 tissues). Thus tissue counts are HC 10, PRI 20, LM 10 and PM 15. No ovarian metastasis group is included. The design contains multiple tissues per patient; tissue counts and patient counts describe different units.

After quality control, 8,000 cells remained (HC 1,000; PRI 2,500; LM 2,000; PM 2,500). B cell subtypes were annotated from marker expression, including tumor-associated atypical B cells (TAAB). Quality control and library batch integration are in Figure S1. Coexpression analysis uses one aggregate per patient, with module summaries in Figure S2 and turquoise module enrichment in Figure S8. CNV inference uses B cells as a diploid reference, with normal/tumor epithelial displays in Figure S4. These annotations are inferred from RNA profiles, without matched DNA or lineage tracing.

Site comparisons use patient-aggregated expression with patient blocking for repeated tissues. Figure 1C GSEA ranks all malignant epithelial cells aggregated by patient for LM versus PM. Figure S7D GSEA instead ranks only high-EMT epithelial cells aggregated by patient for LM versus PRI. Both use the same fixed 30-gene SYN_HALLMARK_TNFA_NFKB gene set, but different contrasts and cell subsets. NES and nominal P are reported for each analysis; this local material lacks the ranking tables needed to rerun GSEA.

## p2: Results

Patient-aggregated median TAAB fractions among B cells were HC 0.08, PRI 0.12, LM 0.16 and PM 0.10 (Figure 1B). These descriptive results suggest tissue-site association; no causal intervention was performed. All-malignant-epithelial LM-versus-PM GSEA returned TNF-alpha signaling via NF-kB NES 3.20, nominal P 0.001 (Figure 1C). The separate high-EMT LM-versus-PRI analysis returned NES 1.65, nominal P 0.04 (Figure S7D).

The expression-derived CD86-CD28 candidate network connects TAAB to CD4 T cells (Figure S3). MIF-CD74-CXCR4 candidate communication is stronger from epithelial cells to TAAB than in the reverse direction, according to modeled probabilities (Figure S5). MIF is broadly detected across the displayed cell populations, with higher mean expression in epithelial and metastatic-site populations than in memory T and CD8 T populations. CD74 is highest in TAAB (Figure S7F). Broad detection does not imply equal mean expression. Expression-derived edges do not directly measure secretion, physical contact or functional necessity.

TAAB exhibited lower developmental potency than the other B cell phenotypes (Figure S6F). CytoTRACE-like maps display potency and relative differentiation order in Figure S6C-D. Because this is cross-sectional RNA profiling, the trajectory does not establish an observed lineage transition. Patient-specific confounding, modest cohort size and inferred annotations limit mechanistic interpretation.

## p3: Figure 1 legend

![Figure 1](figure1.png)

**Figure 1.** A, tissue counts by site, totaling 55 samples from 20 patients. B, patient-aggregated median TAAB fraction among B cells. C, GSEA in all malignant epithelial patient aggregates comparing LM with PM; NES 3.20 and nominal P 0.001. The comparison differs from the high-EMT LM-versus-PRI GSEA in Figure S7D. Data are observational and descriptive; no mutation or cytokine perturbation is performed.

All eight supplementary figures and their captions are supplied together in supplement.md and local figure-s1.png through figure-s8.png. There is no later supplement release.
"""


SUPPLEMENT = """# Supplementary figures

Original synthetic local reading material, coverage-v1. All supplementary source images are present with this document. No raw expression matrix, analysis code or external record is provided.

## s-p1: Figure S1. Quality control and patient integration

![Figure S1](figure-s1.png)

A, median detected genes after filtering by tissue site. B, UMAP after library batch correction, displaying cells from all 20 patients P01-P20. C, retained cells and library provenance. A patient may contribute multiple site-specific tissue libraries; batch correction used the library identifier. Mixing is a diagnostic visualization rather than proof of correct biological integration.

## s-p2: Figure S2. Patient-level coexpression modules

![Figure S2](figure-s2.png)

A, module eigengene associations with TNF score, TAAB fraction and EMT score across 20 patient aggregates. B, module sizes and network scope. The 30-gene turquoise module has r = 0.62 with the TNF score. Associations are exploratory and do not establish a unique causal pathway.

## s-p3: Figure S3. CD86 communication network

![Figure S3](figure-s3.png)

A, CD86-CD28 inferred network; each edge runs from the CD86-expressing source to the CD28-expressing target. B, modeled communication probabilities. C, scope of the inference. TAAB-to-CD4 T probability is 0.30; no spatial or perturbation validation is supplied.

## s-p4: Figure S4. Copy number inference and epithelial identity

![Figure S4](figure-s4.png)

A, normalized CNV amplitude for normal and tumor epithelial cells, with B cell reference. B, normal (blue) and tumor (red) embedding by inferred CNV status. C, nine expression-gene clusters C1-C9, totaling 90 genes. The chromosome-block heatmap describes CNV inference and the cluster summary describes gene programs, not cell proportions.

## s-p5: Figure S5. Tissue-site communication differences

![Figure S5](figure-s5.png)

A, expression-derived probabilities across PRI, LM and PM. Rows explicitly identify source and target. MIF-CD74-CXCR4 is higher for epithelial-to-TAAB edges than for TAAB-to-epithelial edges. B, tissue labels and inference scope. Communication probability does not measure ligand secretion or prove a necessary mechanism.

## s-p6: Figure S6. B cell developmental potency

![Figure S6](figure-s6.png)

C, potency score, from differentiated (0) to totipotent (1). D, relative order, from more differentiated (0) to less differentiated (1). C and D use separate color palettes. F, median potency by phenotype, with TAAB exhibiting lower potential than other B cell phenotypes. The scores are model-derived properties of RNA profiles rather than functional stemness assays.

## s-p7: Figure S7. Site-associated pathways and ligand-receptor expression

![Figure S7](figure-s7.png)

A, GSVA t values for LM minus PM; red denotes pathways high in PM and blue denotes pathways high in LM. D, GSEA of the high-EMT epithelial subset for LM versus PRI, NES 1.65 and nominal P 0.04 for the fixed SYN_HALLMARK_TNFA_NFKB gene set. This is a different analysis from Figure 1C, which compares all malignant epithelial LM with PM. F, CD74, CXCR4 and MIF expression across TAAB, low-EMT epithelial, high-EMT epithelial, PM epithelial, LM epithelial, memory T, CD8 T and regulatory T cells. Dot color indicates mean expression and dot size indicates the percentage with detectable expression; labels provide both values.

## s-p8: Figure S8. Turquoise module pathway enrichment

![Figure S8](figure-s8.png)

A, KEGG-style overrepresentation in 30 turquoise genes against 1,000 tested expressed background genes, with BH correction across 12 pathways. B, input scope. Enrichment of pathway membership does not directly establish pathway activation in a specific cell population.
"""


GOLD = {
    "fixture_version": "coverage-v1",
    "not_reader_input": True,
    "source_kind": "Original synthetic all-local material; not a real manuscript or data analysis",
    "reader_inputs": ["main.md", "supplement.md", "figure1.png"] + [f"figure-s{i}.png" for i in range(1, 9)],
    "availability": "All 11 reader-input files are available from the outset; none staged or missing",
    "expected_observations": {
        "S1": "QC and library batch integration, 20 patients represented; 55 nested tissue libraries",
        "S2": "Patient-level module associations, turquoise 30 genes and TNF r=.62",
        "S3": "CD86-CD28 expression-inferred network; TAAB -> CD4 T .30",
        "S4": "CNV amplitude, normal/tumor embedding and C1-C9 gene clusters; not cell proportions",
        "S5": "PRI/LM/PM communication with explicit source -> target; epi -> TAAB higher than reverse",
        "S6": "Both endpoints encode high potential / less differentiation; TAAB .55 highest",
        "S7": "Actual blue highPM/red highLM; MIF expression highest epi/PM/LM, detected in all columns",
        "S8": "Turquoise KEGG-style overrepresentation, background1000, BH12; not direct activation",
    },
    "true_conflicts": [
        {"id": "C01_potency", "text_locations": ["main.md p2", "supplement.md s-p6 F"],
         "image_location": "figure-s6.png F", "fact": "Text/caption say TAAB lower, but TAAB median .55 is highest; other medians .07-.13",
         "pass": "Flag unresolved text/caption-versus-image direction conflict; avoid silently correcting text or concluding real stemness"},
        {"id": "C02_GSVA_colors", "text_locations": ["supplement.md s-p7 A"],
         "image_location": "figure-s7.png A", "fact": "Caption red highPM/blue highLM; actual plot blue highPM/red highLM",
         "pass": "Independently read plot labels, identify caption reversal; do not copy caption as a verified image observation"},
    ],
    "no_conflicts": [
        {"id": "N01_units", "fact": "20 patients supply55 tissues:10*4+5*2+5*1=55; HC10 PRI20 LM10 PM15",
         "fail": "Calls55samples versus20patients a sample-count contradiction or treats8000cells as patients"},
        {"id": "N02_potency_endpoints", "fact": "S6C1Totipotent and S6D1Less diff both mean less differentiated/highpotential; palettes differ",
         "fail": "Calls endpoint semantics opposite solely from wording or color palette"},
        {"id": "N03_MIF_breadth", "fact": "MIF detected50-90% in every population, while mean expression varies; memoryT/CD8T are pale, not highest",
         "fail": "Equates broadly detected with uniformly expressed, reverses columns, or infers T-cell MIF source from this plot"},
        {"id": "N04_NES_identity", "fact": "Fig1C3.20/P.001:all malignant epi LM-PM; S7D1.65/P.04:highEMT epi LM-PRI; samegeneset different contrasts/subsets",
         "fail": "Treats different NES/P values as automatic contradiction without aligning comparison and cell subset"},
    ],
    "coverage_pass": "Actually open all9 image files; record all8 supplements as image-inspected with brief figure-specific observations. Captions/file listing alone are not image inspection. Any unreadable panel must identify a real encountered limitation.",
    "evaluation": "Grade against actual read/view trace and located report passages. Run reader with only reader_inputs copied into a clean directory; never provide gold.json or generator. Static titles/keywords alone do not establish passing behavior. Preserve first-run failures separately from revisions.",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for make in [figure1, figure_s1, figure_s2, figure_s3, figure_s4, figure_s5,
                 figure_s6, figure_s7, figure_s8]:
        make()
    (OUT / "main.md").write_text(MAIN, encoding="utf-8")
    (OUT / "supplement.md").write_text(SUPPLEMENT, encoding="utf-8")
    (OUT / "gold.json").write_text(json.dumps(GOLD, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Generated 11 reader inputs and evaluator-only gold.json in {OUT}")


if __name__ == "__main__":
    main()
