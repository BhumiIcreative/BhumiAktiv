from odoo import api, fields, models


class SurveyQuestionAnswer(models.Model):
    _inherit = 'survey.question.answer'

    ai_weight = fields.Float(string='AI Weight', default=0.0)


class SurveyUserInput(models.Model):
    _inherit = 'survey.user_input'

    @api.model_create_multi
    def create(self, vals_list):
        inputs = super().create(vals_list)
        inputs._link_to_applicant_if_needed()
        return inputs

    def write(self, vals):
        result = super().write(vals)
        if vals.get('state') == 'done':
            self._compute_ai_score_on_done()
        return result

    def _link_to_applicant_if_needed(self):
        applicant_model = self.env['hr.applicant'].sudo()
        for user_input in self:
            applicant = applicant_model.search([
                ('ai_survey_id', '=', user_input.survey_id.id),
                ('ai_survey_input_id', '=', False),
            ], limit=1)
            if applicant:
                applicant.ai_survey_input_id = user_input.id

    def _compute_ai_score_on_done(self):
        applicant_model = self.env['hr.applicant'].sudo()
        for user_input in self:
            applicant = applicant_model.search([('ai_survey_input_id', '=', user_input.id)], limit=1)
            if not applicant:
                continue
            weighted_score = self._calculate_weighted_score(user_input)
            applicant.write({'ai_survey_score': weighted_score})
            applicant._ai_finalize_evaluation()

    def _calculate_weighted_score(self, user_input):
        lines = user_input.user_input_line_ids.filtered(lambda l: l.suggested_answer_id)
        if not lines:
            return 0.0
        total_weights = sum(lines.mapped('suggested_answer_id.ai_weight'))
        max_weights = sum(
            max(line.question_id.suggested_answer_ids.mapped('ai_weight') or [0.0])
            for line in lines
        )
        if max_weights <= 0:
            return 0.0
        return round((total_weights / max_weights) * 100.0, 2)
