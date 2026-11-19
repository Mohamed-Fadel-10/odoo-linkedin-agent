from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_linkedin_url = fields.Char(
        string="LinkedIn Profile URL",
        index=True,
        copy=False,
        help="LinkedIn profile URL of this contact.",
    )