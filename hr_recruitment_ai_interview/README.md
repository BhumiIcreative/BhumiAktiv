# Recruitment AI Interview Automation (Odoo 19)

This module extends **Recruitment** and **Survey** to automate:

1. CV parsing and applicant field autofill (only existing applicant fields are updated).
2. CV-vs-Job Description scoring (0-100) and summary generation.
3. Auto-generation of weighted survey questions linked to the application.
4. Sending survey link and moving application to **AI Interview** stage.
5. Post-submission weighted scoring and stage progression.
6. Final recommendation from CV + survey + JD.

## Notes about Odoo internal AI

The module intentionally calls only internal Odoo AI models if available (`ai.service` or `ai.model`).
If unavailable, safe fallback logic is used so installation and core flows do not crash.

## Dependencies

- `hr_recruitment`
- `survey`
- `mail`

## Main fields added on applicant

- `ai_cv_score`
- `ai_survey_score`
- `ai_total_score`
- `ai_summary`
- `ai_recommendation`
- `ai_survey_id`
- `ai_survey_input_id`
- `ai_cv_processed`

## Manual flow

- Upload CV on applicant (or website-created applicant with CV): processing runs automatically.
- Click **Send AI Survey** to send survey link and move stage to AI Interview.
- When survey is submitted, score updates and recommendation is generated.
