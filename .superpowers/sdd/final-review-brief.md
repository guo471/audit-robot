# Final Adversarial Review Brief

Review the current working tree of `C:\Users\HUAWEI\Desktop\audit_robot` read-only. Do not edit, format, commit, revert, or generate reports.

Approved changes only:
1. Verified home appliances must ignore model `activation_photo_ok=false` after first-stage SN verification; ordinary 3C/computer remain unchanged. Home still enforces product, unboxing/installation, invoice, duplicate, type, and authenticity failures.
2. Every audit mode uses one absolute 60-second model budget per order. Calls/retries share remaining time; 5-second connect timeout remains a bounded sub-timeout.
3. PowerShell uses explicit UTF-8, project Python/cv2 preflight, prompt checks/hashes, and one manifest for first run/network retry. Incompatible manifests cannot merge.
4. Exact top-level runtime/output paths may be ignored; `prompts/` must stay trackable.

Forbidden/unchanged:
- Address, R9, authenticity decisions, duplicate-photo rules, categories, computer rules, SN normalization, second-stage SN equality, early-stop behavior, old report contents/layout.
- No broad refactor or new dependency.

Evidence files:
- `.superpowers/sdd/task-1-report.md`
- `.superpowers/sdd/task-2-report.md`
- `.superpowers/sdd/task-3-report.md`
- `docs/superpowers/plans/2026-07-18-audit-runtime-scoped-repair.md`

Current controller verification: 415 affected tests passed. The worktree was already dirty before this work, so do not treat every Git diff line as part of this repair; use reports, new tests, and added symbols to identify scoped changes.

Required review output:
- Findings first, severity Critical/Important/Minor, each with exact file/line and reproducible reasoning.
- Explicit verdict on scope compliance and code quality.
- Explicitly check that a reused RunName cannot mutate an old run before the script rejects it, that timeout exceptions become per-order results rather than aborting a batch, and that the manifest is truthful in a dirty Git worktree.
- If no findings, state residual risks and test gaps. Write the review to the report path given in your dispatch.
