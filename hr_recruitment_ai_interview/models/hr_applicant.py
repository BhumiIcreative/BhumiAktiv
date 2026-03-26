import base64
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    ai_cv_score = fields.Float(string='AI CV Score', digits=(16, 2), readonly=True)
    ai_survey_score = fields.Float(string='AI Survey Score', digits=(16, 2), readonly=True)
    ai_total_score = fields.Float(string='AI Total Score', digits=(16, 2), readonly=True)
    ai_summary = fields.Text(string='AI Summary', readonly=True)
    ai_recommendation = fields.Text(string='AI Recommendation', readonly=True)
    ai_survey_id = fields.Many2one('survey.survey', string='AI Survey', readonly=True, copy=False)
    ai_survey_input_id = fields.Many2one('survey.user_input', string='AI Survey Attempt', readonly=True, copy=False)
    ai_cv_processed = fields.Boolean(string='AI CV Processed', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        applicants = super().create(vals_list)
        applicants._ai_trigger_cv_processing(event='create')
        return applicants

    def write(self, vals):
        result = super().write(vals)
        trigger_fields = {'attachment_ids', 'job_id', 'description'}
        if trigger_fields.intersection(vals.keys()) and not self.env.context.get('skip_ai_cv_process'):
            self._ai_trigger_cv_processing(event='write')
        return result

    def action_send_ai_survey(self):
        mail_mail = self.env['mail.mail'].sudo()
        ai_stage = self._get_or_create_ai_stage()
        for applicant in self:
            applicant.ensure_one()
            if not applicant.ai_survey_id:
                applicant._ai_generate_survey()
            survey_input = applicant._ai_get_or_create_survey_input()
            if applicant.email_from and survey_input:
                link = applicant._ai_build_survey_link(survey_input)
                mail_values = {
                    'subject': _('AI Interview Survey - %s', applicant.partner_name or applicant.name),
                    'email_to': applicant.email_from,
                    'body_html': '<p>%s</p><p><a href="%s">%s</a></p>' % (
                        _('Please complete your AI Interview survey from the link below:'),
                        link,
                        _('Open Survey'),
                    ),
                }
                mail_mail.create(mail_values).send()
            if ai_stage:
                applicant.stage_id = ai_stage.id
        return True

    def _ai_trigger_cv_processing(self, event='write'):
        for applicant in self:
            if applicant.env.context.get('skip_ai_cv_process'):
                continue
            cv_text = applicant._ai_extract_cv_text()
            if not cv_text:
                continue
            parsed_data = applicant._ai_parse_cv(cv_text)
            updates = applicant._ai_prepare_field_updates(parsed_data)
            score_payload = applicant._ai_score_cv_against_job(cv_text)
            updates.update({
                'ai_cv_score': score_payload.get('score', 0.0),
                'ai_summary': score_payload.get('summary') or applicant.ai_summary,
                'ai_cv_processed': True,
                'ai_total_score': score_payload.get('score', 0.0),
            })
            applicant.with_context(skip_ai_cv_process=True).write(updates)
            if event == 'create' or not applicant.ai_survey_id:
                applicant._ai_generate_survey(cv_text=cv_text, cv_summary=updates.get('ai_summary'))

    def _ai_extract_cv_text(self):
        self.ensure_one()
        attachments = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.applicant'),
            ('res_id', '=', self.id),
        ], order='id desc')
        for attachment in attachments:
            if not self._looks_like_cv(attachment):
                continue
            if getattr(attachment, 'index_content', False):
                return attachment.index_content
            if attachment.mimetype and attachment.mimetype.startswith('text') and attachment.datas:
                try:
                    return base64.b64decode(attachment.datas).decode('utf-8', errors='ignore')
                except Exception as err:
                    _logger.debug('Unable to decode text attachment %s: %s', attachment.id, err)
        if self.description:
            return html2plaintext(self.description)
        return False

    def _looks_like_cv(self, attachment):
        name = (attachment.name or '').lower()
        mimetype = attachment.mimetype or ''
        return any(token in name for token in ['cv', 'resume']) or mimetype in {
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain',
        }

    def _ai_parse_cv(self, cv_text):
        self.ensure_one()
        prompt = (
            'Extract candidate details from CV and return JSON with keys: '
            'name, email, phone, summary, skills, experience_years.'
        )
        raw = self._call_internal_ai(prompt=prompt, context_payload={'cv_text': cv_text})
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return {
                'name': self.partner_name or self.name,
                'email': self.email_from,
                'phone': self.partner_phone,
                'summary': (cv_text or '')[:500],
                'skills': [],
                'experience_years': 0,
            }

    def _ai_prepare_field_updates(self, parsed_data):
        self.ensure_one()
        updates = {}
        field_mapping = {
            'name': 'partner_name',
            'email': 'email_from',
            'phone': 'partner_phone',
            'summary': 'description',
        }
        for parsed_key, odoo_field in field_mapping.items():
            if odoo_field in self._fields and parsed_data.get(parsed_key):
                updates[odoo_field] = parsed_data[parsed_key]
        return updates

    def _ai_score_cv_against_job(self, cv_text):
        self.ensure_one()
        job_description = html2plaintext(self.job_id.description or '') if self.job_id else ''
        prompt = (
            'Compare CV and Job Description and return JSON: '
            '{"score": number_0_to_100, "summary": "short summary"}.'
        )
        payload = self._call_internal_ai(prompt=prompt, context_payload={
            'cv_text': cv_text,
            'job_description': job_description,
        })
        if isinstance(payload, dict):
            score = float(payload.get('score', 0.0))
            return {
                'score': max(0.0, min(score, 100.0)),
                'summary': payload.get('summary') or '',
            }
        # deterministic fallback
        cv_words = set(re.findall(r'\w+', (cv_text or '').lower()))
        job_words = set(re.findall(r'\w+', (job_description or '').lower()))
        overlap = len(cv_words & job_words)
        denom = len(job_words) or 1
        score = round(min(100.0, (overlap / denom) * 100), 2)
        return {
            'score': score,
            'summary': _('Keyword overlap score generated by fallback evaluator.'),
        }

    def _ai_generate_survey(self, cv_text=None, cv_summary=None):
        survey_model = self.env['survey.survey'].sudo()
        question_model = self.env['survey.question'].sudo()
        answer_model = self.env['survey.question.answer'].sudo()
        for applicant in self:
            if applicant.ai_survey_id:
                continue
            title = _('AI Interview - %s', applicant.partner_name or applicant.name)
            survey = survey_model.create({
                'title': title,
                'access_mode': 'token',
                'questions_layout': 'page_per_question',
            })
            questions_payload = applicant._ai_generate_questions_payload(cv_text=cv_text, cv_summary=cv_summary)
            for question in questions_payload:
                survey_question = question_model.create({
                    'survey_id': survey.id,
                    'title': question.get('title'),
                    'question_type': 'simple_choice',
                    'is_scored_question': True,
                })
                for answer in question.get('answers', []):
                    answer_model.create({
                        'question_id': survey_question.id,
                        'value': answer.get('text'),
                        'ai_weight': float(answer.get('weight', 0.0)),
                    })
            applicant.ai_survey_id = survey.id

    def _ai_generate_questions_payload(self, cv_text=None, cv_summary=None):
        self.ensure_one()
        prompt = (
            'Generate 5 interview MCQ questions as JSON list. '
            'Each item: {"title": str, "answers": [{"text": str, "weight": number}], '
            '"answers" must have 4 options and all questions must have exactly same options.'
        )
        payload = self._call_internal_ai(prompt=prompt, context_payload={
            'cv_text': cv_text or self.ai_summary or '',
            'cv_summary': cv_summary or self.ai_summary or '',
            'job_description': html2plaintext(self.job_id.description or '') if self.job_id else '',
        })
        if isinstance(payload, list) and payload:
            return payload
        default_answers = [
            {'text': _('Strongly Aligned'), 'weight': 100},
            {'text': _('Partially Aligned'), 'weight': 70},
            {'text': _('Basic Understanding'), 'weight': 40},
            {'text': _('Not Aligned'), 'weight': 0},
        ]
        return [
            {'title': _('How well do you match the core requirements of this role?'), 'answers': default_answers},
            {'title': _('How confident are you with the required technical stack?'), 'answers': default_answers},
            {'title': _('How experienced are you in similar responsibilities?'), 'answers': default_answers},
            {'title': _('How effective are your communication and collaboration skills?'), 'answers': default_answers},
            {'title': _('How soon can you contribute independently in this position?'), 'answers': default_answers},
        ]

    def _ai_get_or_create_survey_input(self):
        self.ensure_one()
        if self.ai_survey_input_id:
            return self.ai_survey_input_id
        if not self.ai_survey_id:
            return False
        vals = {
            'survey_id': self.ai_survey_id.id,
            'email': self.email_from or '',
            'partner_id': self.partner_id.id if self.partner_id else False,
        }
        user_input = self.env['survey.user_input'].sudo().create(vals)
        self.ai_survey_input_id = user_input.id
        return user_input

    def _ai_build_survey_link(self, user_input):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        survey = user_input.survey_id
        return '%s/survey/start/%s?answer_token=%s' % (base_url, survey.access_token, user_input.access_token)

    def _get_or_create_ai_stage(self):
        stage = self.env.ref('hr_recruitment_ai_interview.hr_recruitment_stage_ai_interview', raise_if_not_found=False)
        if stage:
            return stage
        return self.env['hr.recruitment.stage'].sudo().create({
            'name': _('AI Interview'),
            'sequence': 70,
        })

    def _ai_finalize_evaluation(self):
        for applicant in self:
            job_description = html2plaintext(applicant.job_id.description or '') if applicant.job_id else ''
            prompt = (
                'Evaluate candidate using CV summary, survey score, and JD. '
                'Return JSON {"recommendation": str, "overall_score": number_0_to_100}.'
            )
            payload = applicant._call_internal_ai(prompt=prompt, context_payload={
                'cv_summary': applicant.ai_summary or '',
                'survey_score': applicant.ai_survey_score,
                'job_description': job_description,
            })
            recommendation = ''
            total_score = applicant.ai_total_score
            if isinstance(payload, dict):
                recommendation = payload.get('recommendation', '')
                total_score = max(0.0, min(float(payload.get('overall_score', total_score)), 100.0))
            else:
                recommendation = _('Final recommendation generated using weighted CV and survey scores.')
                total_score = round((applicant.ai_cv_score * 0.6) + (applicant.ai_survey_score * 0.4), 2)

            next_stage = applicant._find_next_stage()
            vals = {
                'ai_recommendation': recommendation,
                'ai_total_score': total_score,
            }
            if next_stage:
                vals['stage_id'] = next_stage.id
            applicant.write(vals)

    def _find_next_stage(self):
        self.ensure_one()
        if not self.stage_id:
            return False
        domain = [('sequence', '>', self.stage_id.sequence)]
        if self.job_id:
            domain = ['|', ('job_ids', '=', False), ('job_ids', 'in', self.job_id.id)] + domain
        return self.env['hr.recruitment.stage'].search(domain, order='sequence asc', limit=1)

    def _call_internal_ai(self, prompt, context_payload):
        """Use Odoo internal AI endpoint/service if available.

        This method intentionally avoids external providers and gracefully
        falls back when the internal AI service is unavailable.
        """
        ai_service = self.env['ai.service'] if 'ai.service' in self.env else False
        if ai_service and hasattr(ai_service, 'generate_json'):
            try:
                return ai_service.generate_json(prompt=prompt, payload=context_payload)
            except Exception as err:
                _logger.warning('Internal AI service failed: %s', err)

        ai_model = self.env['ai.model'] if 'ai.model' in self.env else False
        if ai_model and hasattr(ai_model, 'execute_prompt'):
            try:
                return ai_model.execute_prompt(prompt=prompt, payload=context_payload)
            except Exception as err:
                _logger.warning('Internal AI model failed: %s', err)

        _logger.info('Internal Odoo AI API not found. Using fallback evaluator.')
        return False
