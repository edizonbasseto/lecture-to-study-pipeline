# Demo lecture results

Measured on 2026-10-16. Every rate is printed with its denominator; the method, the word error rate definition and the limits of a single evidence pack are recorded in [ADR 0047](../decisions/0047-demo-lecture-measurement-method.md).

## Lecture

- Video duration: 243.6 s
- Reference words: 620
- Evidence packs (codex): 1
- Evidence packs (llama-cpp): 1
- Note (codex): Codex model note: the default model of the codex CLI configuration is not accepted for a ChatGPT account, so this run used the supported model gpt-6-sol; the earlier failed attempts of phase 5 produced no model output.
- Note (llama-cpp): Exams stage: attempt 1 failed its contract (5 calls, 1 retry); the figures are those of the successful rerun.
- Note (llama-cpp): Flashcards stage: attempts 1 and 2 at the default deck floor of 12 failed (9 calls with 4 retries, then 3 calls with 1 retry); the successful run used a floor of 5 by operator decision, so it made one request and no supplemental ones.

The demo lecture fixes one evidence pack per adapter (ADR 0047), so each critic rate rests on a small number of reviews.

## ASR word error rate

| Adapter | Lecture id | Condition | WER before dictionary | WER after dictionary | Corrections applied |
| --- | --- | --- | --- | --- | --- |
| codex | 925101 | pipeline (vocabulary prompt on) | 2.3% (14 of 620 words) | 0.5% (3 of 620 words) | 11 |
| codex | 925101 | ablation (vocabulary prompt off) | 3.1% (19 of 620 words) | 0.0% (0 of 620 words) | 19 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | 2.3% (14 of 620 words) | 0.5% (3 of 620 words) | 11 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | 3.1% (19 of 620 words) | 0.0% (0 of 620 words) | 19 |

### Planted terms

| Adapter | Lecture id | Condition | Term | In reference | Before dictionary | After dictionary |
| --- | --- | --- | --- | --- | --- | --- |
| codex | 925101 | pipeline (vocabulary prompt on) | biópsia excisional | 4 | 4 | 4 |
| codex | 925101 | pipeline (vocabulary prompt on) | histopatológico | 8 | 8 | 8 |
| codex | 925101 | pipeline (vocabulary prompt on) | camada fascial | 5 | 0 | 5 |
| codex | 925101 | pipeline (vocabulary prompt on) | plano fascial | 3 | 0 | 3 |
| codex | 925101 | pipeline (vocabulary prompt on) | margem fascial | 3 | 0 | 3 |
| codex | 925101 | pipeline (vocabulary prompt on) | margem profunda | 5 | 5 | 5 |
| codex | 925101 | pipeline (vocabulary prompt on) | margem lateral | 6 | 6 | 6 |
| codex | 925101 | pipeline (vocabulary prompt on) | mastocitoma | 1 | 1 | 1 |
| codex | 925101 | pipeline (vocabulary prompt on) | sarcoma de tecidos moles | 1 | 1 | 1 |
| codex | 925101 | pipeline (vocabulary prompt on) | citologia aspirativa | 1 | 1 | 1 |
| codex | 925101 | pipeline (vocabulary prompt on) | tecido subcutâneo | 2 | 2 | 2 |
| codex | 925101 | pipeline (vocabulary prompt on) | linfonodo regional | 2 | 2 | 2 |
| codex | 925101 | pipeline (vocabulary prompt on) | formalina | 2 | 2 | 2 |
| codex | 925101 | pipeline (vocabulary prompt on) | hemostasia | 3 | 3 | 3 |
| codex | 925101 | pipeline (vocabulary prompt on) | nervo facial | 1 | 1 | 1 |
| codex | 925101 | ablation (vocabulary prompt off) | biópsia excisional | 4 | 4 | 4 |
| codex | 925101 | ablation (vocabulary prompt off) | histopatológico | 8 | 0 | 8 |
| codex | 925101 | ablation (vocabulary prompt off) | camada fascial | 5 | 0 | 5 |
| codex | 925101 | ablation (vocabulary prompt off) | plano fascial | 3 | 0 | 3 |
| codex | 925101 | ablation (vocabulary prompt off) | margem fascial | 3 | 0 | 3 |
| codex | 925101 | ablation (vocabulary prompt off) | margem profunda | 5 | 5 | 5 |
| codex | 925101 | ablation (vocabulary prompt off) | margem lateral | 6 | 6 | 6 |
| codex | 925101 | ablation (vocabulary prompt off) | mastocitoma | 1 | 1 | 1 |
| codex | 925101 | ablation (vocabulary prompt off) | sarcoma de tecidos moles | 1 | 1 | 1 |
| codex | 925101 | ablation (vocabulary prompt off) | citologia aspirativa | 1 | 1 | 1 |
| codex | 925101 | ablation (vocabulary prompt off) | tecido subcutâneo | 2 | 2 | 2 |
| codex | 925101 | ablation (vocabulary prompt off) | linfonodo regional | 2 | 2 | 2 |
| codex | 925101 | ablation (vocabulary prompt off) | formalina | 2 | 2 | 2 |
| codex | 925101 | ablation (vocabulary prompt off) | hemostasia | 3 | 3 | 3 |
| codex | 925101 | ablation (vocabulary prompt off) | nervo facial | 1 | 1 | 1 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | biópsia excisional | 4 | 4 | 4 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | histopatológico | 8 | 8 | 8 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | camada fascial | 5 | 0 | 5 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | plano fascial | 3 | 0 | 3 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | margem fascial | 3 | 0 | 3 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | margem profunda | 5 | 5 | 5 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | margem lateral | 6 | 6 | 6 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | mastocitoma | 1 | 1 | 1 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | sarcoma de tecidos moles | 1 | 1 | 1 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | citologia aspirativa | 1 | 1 | 1 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | tecido subcutâneo | 2 | 2 | 2 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | linfonodo regional | 2 | 2 | 2 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | formalina | 2 | 2 | 2 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | hemostasia | 3 | 3 | 3 |
| llama-cpp | 925100 | pipeline (vocabulary prompt on) | nervo facial | 1 | 1 | 1 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | biópsia excisional | 4 | 4 | 4 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | histopatológico | 8 | 0 | 8 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | camada fascial | 5 | 0 | 5 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | plano fascial | 3 | 0 | 3 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | margem fascial | 3 | 0 | 3 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | margem profunda | 5 | 5 | 5 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | margem lateral | 6 | 6 | 6 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | mastocitoma | 1 | 1 | 1 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | sarcoma de tecidos moles | 1 | 1 | 1 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | citologia aspirativa | 1 | 1 | 1 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | tecido subcutâneo | 2 | 2 | 2 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | linfonodo regional | 2 | 2 | 2 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | formalina | 2 | 2 | 2 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | hemostasia | 3 | 3 | 3 |
| llama-cpp | 925100 | ablation (vocabulary prompt off) | nervo facial | 1 | 1 | 1 |

## LLM stages

### codex (925101)

| Stage | Record status | First-attempt schema pass rate | Retries | Wall time | Model call time | Model | Engine |
| --- | --- | --- | --- | --- | --- | --- | --- |
| semantic | succeeded | 100.0% (1 of 1 requests) | 0 | 48.1 s | 48.1 s | codex-cli | n/a |
| drafting | succeeded | 100.0% (1 of 1 requests) | 0 | 19.7 s | 19.7 s | codex-cli | n/a |
| critic | succeeded | 100.0% (1 of 1 requests) | 0 | 21.1 s | 21.1 s | codex-cli | n/a |
| exams | succeeded | 100.0% (16 of 16 requests) | 0 | 222.7 s | 222.7 s | codex-cli | n/a |
| podcast | succeeded | 100.0% (1 of 1 requests) | 0 | 19.9 s | 19.9 s | codex-cli | n/a |
| flashcards | succeeded | 100.0% (8 of 8 requests) | 0 | 98.4 s | 98.4 s | codex-cli | n/a |

### llama-cpp (925100)

| Stage | Record status | First-attempt schema pass rate | Retries | Wall time | Model call time | Model | Engine |
| --- | --- | --- | --- | --- | --- | --- | --- |
| semantic | succeeded | 100.0% (1 of 1 requests) | 0 | 53.5 s | 51.7 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |
| drafting | succeeded | 100.0% (1 of 1 requests) | 0 | 22.9 s | 21.3 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |
| critic | succeeded | 100.0% (1 of 1 requests) | 0 | 9.5 s | 7.9 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |
| exams | succeeded | 87.5% (14 of 16 requests) | 2 | 232.1 s | 229.7 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |
| podcast | succeeded | 100.0% (1 of 1 requests) | 0 | 24.9 s | 22.4 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |
| flashcards | succeeded | 100.0% (1 of 1 requests) | 0 | 21.9 s | 20.0 s | qwen2:qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf | 91d2fc387529940230555abd297a8b5e99737d3f |

For the exams, podcast and flashcards stages the first-attempt contract also includes the student-facing text guard.

## Critic rejection rate

| Adapter | Record status | Rejection rate | Units | Redrafts | Finally rejected units |
| --- | --- | --- | --- | --- | --- |
| codex | succeeded | 0.0% (0 of 1 reviews) | 1 | 0 | 0 |
| llama-cpp | succeeded | 0.0% (0 of 1 reviews) | 1 | 0 | 0 |

## Per-stage wall time

### codex (925101)

| Phase | Name | Wall time |
| --- | --- | --- |
| 2 | extraction | skipped (resumed) |
| 3 | transcription | skipped (resumed) |
| 4 | evidence | skipped (resumed) |
| 5 | semantic | 48.2 s |
| 6 | drafting | 19.9 s |
| 7 | critic | 21.2 s |
| 8 | assembly | 0.0 s |
| 9 | metadata | 0.0 s |
| 10 | quality-certification | refused by design (no certificate is issued) |
| 13 | exams | 222.9 s |
| 14 | podcast | 20.1 s |
| 15 | tts | 23.3 s |
| 18 | flashcards | 98.4 s |

### llama-cpp (925100)

| Phase | Name | Wall time |
| --- | --- | --- |
| 2 | extraction | 4.9 s |
| 3 | transcription | 6.6 s |
| 4 | evidence | 0.0 s |
| 5 | semantic | 53.7 s |
| 6 | drafting | 23.2 s |
| 7 | critic | 9.7 s |
| 8 | assembly | 0.0 s |
| 9 | metadata | 0.0 s |
| 10 | quality-certification | refused by design (no certificate is issued) |
| 13 | exams | 232.3 s |
| 14 | podcast | 25.2 s |
| 15 | tts | 8.1 s |
| 18 | flashcards | 21.9 s |

Phase 10 (quality-certification) refuses the lecture by design: publication certification stays fail-closed because authoritative evidence is unavailable, so no certificate is issued and the phase has no wall time (ADR 0024).

## Blocked-lecture recovery

Lecture 925100 with the llama-cpp adapter; the last run did not pass (ADR 0045). The scenarios below are those of the last run.

### Attempts

- Five full runs of the recovery were made on the local adapter. In every run and both scenarios the four derivative scripts exited 1 with every output root unchanged, and the critic rerun exited 0 (a real model verdict, no unit rejected again).
- Attempts 1 and 2 used the default flashcards deck floor of 12: podcast and study guide recovered (exit 0), exam and flashcards exited 1 in both scenarios (local model contract failures).
- Attempts 3 to 5 used a flashcards deck floor of 5 by operator decision, the deviation already applied to the harvested run (the recovery driver option was added for it).
- Attempt 3: flashcards, podcast and study guide recovered in both scenarios; exam exited 1 in both (stale: JSON schema validation; rejected: student-facing text guard).
- Attempt 4: rejected scenario fully recovered (exam, podcast, flashcards and study guide all exit 0, all four outputs refreshed); stale scenario exam exited 1 (no JSON array in the model output).
- Attempt 5: flashcards, podcast and study guide recovered in both scenarios; exam exited 1 in both (stale: JSON schema validation; rejected: no JSON array in the model output).
- Overall the exam recovered in 1 of 10 scenario runs and flashcards in 6 of 6 runs at floor 5 (0 of 4 at floor 12); podcast and study guide recovered in 10 of 10. The operator limit of three runs at floor 5 was reached, so no further run was made. The exam failures are contract failures of the local 14B model, not gate or critic outcomes.

### stale draft (a draft newer than its reviewed unit)

- Blocked scripts (exit codes): exam 1, podcast 1, flashcards 1, study_guide 1
- Output roots unchanged by the blocked scripts: yes (changes: none)
- Units deleted: none (every unit has its draft)
- Critic rerun exit code: 0
- Recovered scripts (exit codes): exam 1, podcast 0, flashcards 0, study_guide 0
- Outputs refreshed: podcast, flashcards, study_guide
- Result: did not pass

### rejected review (a unit whose review did not pass)

- Blocked scripts (exit codes): exam 1, podcast 1, flashcards 1, study_guide 1
- Output roots unchanged by the blocked scripts: yes (changes: none)
- Units deleted: none (every unit has its draft)
- Critic rerun exit code: 0
- Recovered scripts (exit codes): exam 1, podcast 0, flashcards 0, study_guide 0
- Outputs refreshed: podcast, flashcards, study_guide
- Result: did not pass
