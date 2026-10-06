# B cell states and site-associated signaling in gastric tissue

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
