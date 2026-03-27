{
    'name': 'Recruitment AI Interview Automation',
    'version': '19.0.1.0.0',
    'summary': 'Automates CV parsing, scoring, survey generation, and AI interview flow.',
    'depends': ['hr_recruitment', 'survey', 'mail'],
    'data': [
        'data/hr_recruitment_stage_data.xml',
        'views/hr_applicant_views.xml',
        'views/survey_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
