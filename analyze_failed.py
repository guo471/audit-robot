import json
import sys

failed_orders = [
    '481172702361737769779288',
    '481173139331953737728049',
    '481173341410837793996882',
    '481173353533504628326480',
    '481173405563867555430460',
    '491169415245669236736019'
]

with open('reports/model_audit/guobu1000_20260721_v1_fixed_run02_combined.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

rows = data.get('rows', [])
print(f'Total rows: {len(rows)}')

found = []
for row in rows:
    order_id = row.get('order_id')
    if order_id in failed_orders:
        found.append(order_id)
        print('='*80)
        print(f'Order ID: {order_id}')
        print(f'Manual flag: {row.get("manual_flag")}')
        print(f'Manual reason code: {row.get("manual_reason_code", "N/A")}')
        print(f'Photo authenticity mode: {row.get("photo_authenticity_mode", "N/A")}')
        print(f'Photo auth would_manual: {row.get("photo_authenticity_would_manual", "N/A")}')

        auth_results = row.get('photo_authenticity_image_results', {})
        print(f'\nAuthenticity results ({len(auth_results)} images):')
        for img_id, result in auth_results.items():
            print(f'  {img_id}: {result.get("result")} - rule={result.get("rule")} status={result.get("status")}')

        compliance = row.get('compliance_result', {})
        auth_by_image = compliance.get('photo_authenticity_by_image') if isinstance(compliance, dict) else None
        if auth_by_image:
            print('\nModel raw observations:')
            for obs in auth_by_image:
                print(f'\n  Image: {obs.get("image_id")}')
                print(f'    edges: {obs.get("edges")}')
                print(f'    screen_owner: {obs.get("screen_owner")}')
                print(f'    strong_evidence: {obs.get("strong_evidence")}')
                print(f'    weak_evidence: {obs.get("weak_evidence")}')
                reason = obs.get('reason', '')[:200]
                print(f'    reason: {reason}')
        else:
            print(f'\nNo photo_authenticity_by_image in compliance result.')
        print()

print(f'\nFound {len(found)}/{len(failed_orders)} orders.')
missing = [o for o in failed_orders if o not in found]
if missing:
    print(f'Missing: {missing}')
