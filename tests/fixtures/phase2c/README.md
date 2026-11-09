# Offline Phase 2C/2D regression inputs

Frozen on 2026-11-07 from the saved `work/production/pNNNN-translation.json`
responses for The Secrets of Astrology, after the Phase 2C retranslation.
`composition_corrections.json` retains only the saved p9 quotation correction,
including its original request, source/style hashes and model provenance.
No text was translated or edited to create these fixtures.

Tests read these files instead of mutable production artifacts. The original
PDF, approved glossary/style and source-bound profile are read-only integration
dependencies. Recompositions and QA output use disposable temporary directories.
The p9 translation payload represents the fragmented input; applying the frozen
correction yields local QA success, while omitting it must still report
`fragmented_display_quote`. Neither case depends on the live correction file.

These are saved model responses for offline tests, not new approval records or
new model/VLM evidence. `sha256.json` records the frozen input bytes.
