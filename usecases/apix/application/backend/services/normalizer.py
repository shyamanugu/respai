"""
services/normalizer.py — Report JSON schema normalization
==========================================================
Handles backward compatibility between old and new report formats.
"""

from backend.config.logging import get_logger

logger = get_logger(__name__)


def normalize_report(raw_report: dict) -> dict:
    """
    Normalize report JSON to handle both old and new schema formats.
    This ensures backward compatibility across JSON structure changes.
    """
    logger.debug("Normalizing report (keys: %s)", list(raw_report.keys())[:8])
    normalized = raw_report.copy()

    # Normalize coaching tips: handle both 'tips' and 'coaching_tips'
    if 'coaching_tips' in raw_report and 'tips' not in raw_report:
        normalized['tips'] = []
        for coaching_tip in raw_report.get('coaching_tips', []):
            tip_obj = {
                'tip': coaching_tip.get('tip', ''),
                'priority': coaching_tip.get('priority', 'Medium'),
                'actionable_steps': coaching_tip.get('actionable_steps', []),
                'expected_impact': coaching_tip.get('expected_impact', ''),
                'examples': []
            }

            if 'examples' in coaching_tip:
                for example in coaching_tip['examples']:
                    if isinstance(example, dict):
                        example_text = example.get('summary', example.get('contact_id', 'Example'))
                        tip_obj['examples'].append(example_text)
                    else:
                        tip_obj['examples'].append(str(example))

            normalized['tips'].append(tip_obj)
    elif 'tips' in raw_report and 'coaching_tips' not in raw_report:
        for tip in normalized.get('tips', []):
            if 'examples' in tip and isinstance(tip['examples'], list):
                formatted_examples = []
                for ex in tip['examples']:
                    if isinstance(ex, str):
                        formatted_examples.append(ex)
                    else:
                        formatted_examples.append(str(ex))
                tip['examples'] = formatted_examples

    # Ensure default fields exist
    if 'escalations' not in normalized:
        normalized['escalations'] = []
    if 'customer_experience' not in normalized:
        normalized['customer_experience'] = {}
    if 'sales_outcome' not in normalized:
        normalized['sales_outcome'] = {}
    if 'key_improvements' not in normalized:
        normalized['key_improvements'] = []
    if 'behavior_scores' not in normalized:
        normalized['behavior_scores'] = {}
    if 'kpis' not in normalized:
        normalized['kpis'] = []

    return normalized
