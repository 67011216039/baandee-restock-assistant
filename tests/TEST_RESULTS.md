# Test results

Run at 2026-10-26T15:39:02+07:00 · 12/12 passed

| ID | Case | Input | Expected | Actual | Result |
|---|---|---|---|---|---|
| T01 | sku_normalisation | ['sku 0045', 'SKU-61', 'SKU-0099'] | ['SKU-0045', 'SKU-0061', None] | ['SKU-0045', 'SKU-0061', None] | PASS |
| T02 | lead_time_units | ['2 weeks', '7d', '1-2 weeks', 'TBC', ''] | [14, 7, None, None, None] | [14, 7, None, None, None] | PASS |
| T03 | ambiguous_date_not_guessed | ['28/07/2026', '07/08/2026'] | [('2026-07-28', 'corrected'), (None, 'ambiguous')] | [('2026-07-28', 'corrected'), (None, 'ambiguous')] | PASS |
| T04 | duplicate_supplier_goes_to_human | SKU-KT-104 | {'decision': 'HUMAN_REVIEW', 'suggested_qty': None, 'supplier_options': 2} | {'decision': 'HUMAN_REVIEW', 'suggested_qty': None, 'supplier_options': 2} | PASS |
| T05 | no_supplier_goes_to_human | SKU-0048 (stock 0, no supplier) | {'decision': 'HUMAN_REVIEW', 'risk': 'HIGH', 'suggested_qty': None} | {'decision': 'HUMAN_REVIEW', 'risk': 'HIGH', 'suggested_qty': None} | PASS |
| T06 | api_success_changes_result | SKU-0001, Supplier API up (catalog says 7 d) | {'api': 'ok', 'lead_time_days': 13, 'risk': 'HIGH', 'lead_time_source': 'Supplier API (live)'} | {'api': 'ok', 'lead_time_days': 13, 'risk': 'HIGH', 'lead_time_source': 'Supplier API (live)'} | PASS |
| T07 | api_not_found_stops | SKU-0021 (catalog lead time '1-2 weeks', no live record) | {'api': 'not_found', 'decision': 'HUMAN_REVIEW'} | {'api': 'not_found', 'decision': 'HUMAN_REVIEW'} | PASS |
| T08 | api_unavailable_fallback_is_labelled | SKU-0001, Supplier API down | {'api': 'unavailable', 'decision': 'RECOMMEND', 'lead_time_days': 7, 'warning_shown': True, 'lead_time_in_verified': False} | {'api': 'unavailable', 'decision': 'RECOMMEND', 'lead_time_days': 7, 'warning_shown': True, 'lead_time_in_verified': False} | PASS |
| T09 | suggested_qty_respects_moq_and_pack | all SKUs | every RECOMMEND qty >= MOQ and a multiple of pack_size | 13 recommendations checked, violations: none | PASS |
| T10 | store_create_reload_edit_audit | create PR 30 units -> reopen DB -> edit to 50 | {'reloaded_qty': 30, 'override_without_note_blocked': True, 'edited_qty': 50, 'audit': ['create', 'update', 'update']} | {'reloaded_qty': 30, 'override_without_note_blocked': True, 'edited_qty': 50, 'audit': ['create', 'update', 'update']} | PASS |
| T11 | soft_delete_keeps_row | create then soft delete | {'visible': False, 'still_in_db': True, 'status': 'cancelled'} | {'visible': False, 'still_in_db': True, 'status': 'cancelled'} | PASS |
| T12 | raw_data_untouched | data/raw/* vs raw_checksums.sha256 | no file changed | changed: none | PASS |
