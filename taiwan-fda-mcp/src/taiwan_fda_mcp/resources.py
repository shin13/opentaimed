# path: src/taiwan_fda_mcp/resources.py
# brief: Static MCP resources describing Taiwan FDA Rx/OTC insert structure.

RX_INSERT_STRUCTURE_MD = """\
# Taiwan FDA 處方藥仿單結構

_Source: 衛福部 110.09.14 衛授食字第 1101407694 號公告; 林鈺儒《藥品仿單結構化簡介》當代醫藥法規月刊 2023 Vol.150_

## Structure overview

A Rx (處方藥) insert has 15 numbered sections + an optional pre-section 特殊警語
(加框警語 / black box warning) + manufacturer/applicant blocks at the end.

`*` = optional. Optional fields that are not present in a given insert are still
expected to exist in the structure (TFDA: 欄位不得遞補排序), so the wrapper
returns them as empty strings.

```
特殊警語* (pre-section, = 加框警語 / BBW)
特殊性狀* (pre-section)
1.  性狀
    1.1 有效成分及含量
    1.2 賦形劑
    1.3 劑型
    1.4 藥品外觀
2.  適應症
3.  用法及用量
    3.1 用法用量
    3.2 調製方式 (注射劑等需調製才必填)
    3.3 特殊族群用法用量*
4.  禁忌
5.  警語及注意事項
    5.1 警語/注意事項
    5.2 藥物濫用及依賴性*
    5.3 操作機械能力*
    5.4 實驗室檢測*
    5.5 其他注意事項*
6.  特殊族群之用藥*
    6.1 懷孕*
    6.2 哺乳*
    6.3 有生育能力的女性與男性*
    6.4 小兒*
    6.5 老年人*
    6.6 肝功能不全*
    6.7 腎功能不全*
    6.8 其他族群*
7.  交互作用
8.  副作用/不良反應
    8.1 臨床重要副作用/不良反應
    8.2 臨床試驗經驗*
    8.3 上市後經驗*
9.  過量
10. 藥理特性
    10.1 作用機轉
    10.2 藥效藥理特性
    10.3 臨床前安全性資料
11. 藥物動力學特性
12. 臨床試驗資料
13. 包裝及儲存
    13.1 包裝
    13.2 效期
    13.3 儲存條件
    13.4 儲存注意事項*
14. 病人使用須知*
15. 其他*
製造廠 / 分裝廠 / 藥商
```

## Wrapper field-name mapping

| TFDA section | wrapper field name |
|---|---|
| pre-section 特殊警語 (加框警語/BBW) | `special_warning` |
| pre-section 特殊性狀 | `characteristics` |
| 1.1 有效成分及含量 | `ingredients` |
| 1.2 賦形劑 | `excipients` |
| 1.3 劑型 | `form_detail` |
| 1.4 藥品外觀 | `appearance` |
| 2 適應症 | `indication` |
| 3 (整節) | `dosage` |
| 3.1 用法用量 | `dosage_general` |
| 3.2 調製方式 | `dosage_preparation` |
| 3.3 特殊族群用法用量 | `dosage_special_populations` |
| 4 禁忌 | `contraindications` |
| 5 (整節) | `warnings` |
| 5.2-5.5 sub-sections | `abuse_dependence`, `machine_operation`, `lab_tests`, `other_precautions` |
| 6 (整節) | `special_populations` |
| 6.1-6.8 sub-sections | `pregnancy`, `lactation`, `reproductive`, `pediatric`, `geriatric`, `hepatic_impairment`, `renal_impairment`, `other_populations` |
| 7 交互作用 | `interactions` |
| 8 (整節) | `side_effects` |
| 8.1 臨床重要副作用 | `adverse_clinical` |
| 8.2 臨床試驗經驗 | `adverse_trial` |
| 8.3 上市後經驗 | `adverse_postmarketing` |
| 9 過量 | `overdose` |
| 10 (整節) | `pharmacology` |
| 10.1 作用機轉 | `mechanism_of_action` |
| 10.2 藥效藥理特性 | `pharmacodynamics` |
| 10.3 臨床前安全性資料 | `nonclinical_safety` |
| 11 藥物動力學特性 | `pharmacokinetics` |
| 12 臨床試驗資料 | `clinical_trials` |
| 13.1 包裝 | `packaging` |
| 13.2 效期 | `shelf_life` |
| 13.3 儲存條件 | `storage_conditions` |
| 13.4 儲存注意事項 | `storage_cautions` |
| 14 病人使用須知 | `patient_instructions` |
| 15 其他 | `other_info` |

## How to use this from get_package_insert

The default `response_format="key"` returns 8 commonly-needed fields (incl.
`special_warning`). Use specific field names to target sub-sections — e.g.
`fields=["geriatric"]` for §6.5 老年人 alone, or
`fields=["renal_impairment", "dosage_special_populations"]` to combine §6.7
and §3.3 for renal-adjustment questions. `special_warning` (加框警語) is
always returned and must be quoted verbatim when present; when its source XML
element is empty it appears in `confirmed_absent` (= TFDA confirms no BBW).
See ADR-0006 for the schema design rationale.

Every response includes `available_sections` (table of contents) listing every
populated section so you can see what else is available without re-fetching.
"""

OTC_INSERT_STRUCTURE_MD = """\
# Taiwan FDA 非處方藥仿單結構

_Source: 衛福部 105.03.08 部授食字第 1051402838 號公告系列_

## Structure overview

OTC (非處方藥: 成藥 / 乙類成藥 / 甲類成藥 / 指示藥) inserts have 6 sections in a
fixed order. The wrapper auto-detects OTC vs Rx from the insert's `<DTYPE>` and
returns `format="otc"`. OTC reuses numeric section numbers with DIFFERENT
meanings than Rx, so OTC has a SEPARATE field set.

```
特殊性狀* (pre-section)
1. 【成分】
    1.1 有效成分及含量
    1.2 其他成分(賦形劑)
2. 【用途(適應症)】
3. 【使用上注意事項】
    3.1 (有下列情形者) 請勿使用
    3.2 使用前洽醫師診治
    3.3 使用前諮詢醫師藥師藥劑生
    3.4 其他使用上注意事項
4. 【用法用量】
5. 【警語】
    5.1 副作用警示
    5.2 症狀警示
6. 【包裝】
7+. 儲存方式 / 類別 / 許可證號 / 急救及解毒方法 / … (optional tail)
製造廠 / 藥商
```

Note: in real OTC XML, the §3.x precautions and §5.x warning items often carry
their text in nested `<TITLE>` elements rather than `<VALUE>`. The wrapper folds
that title-borne content (with its sub-heading) into the parent field so it is
not lost.

## Wrapper field-name mapping

| TFDA section | wrapper field name | shared with Rx? |
|---|---|---|
| pre-section 特殊性狀 | `characteristics` | shared |
| 1.1 有效成分及含量 | `ingredients` | shared |
| 1.2 其他成分(賦形劑) | `excipients` | shared |
| 2 用途(適應症) | `usage` | OTC-only (≠ Rx `indication`) |
| 3 使用上注意事項 (整節) | `usage_precautions` | OTC-only |
| 4 用法用量 | `directions` | OTC-only (≠ Rx `dosage`) |
| 5 警語 (整節) | `otc_warnings` | OTC-only (≠ Rx `warnings`) |
| 6 包裝 | `packaging` | shared |
| 7+ 儲存方式/類別/適用時機/急救及解毒方法/… | (none) → `additional_sections` | — |

Only the official top-level sections are named. OTC §3.x / §5.x sub-numbering
varies per drug (one insert's §3.2 is 諮詢藥師, another's is 洽醫師), so naming
sub-fields by position would mislabel content. Instead §3 / §5 are returned via
their parents `usage_precautions` / `otc_warnings`, which fold every sub-item's
text and heading. Cite §3 / §5 (not a sub-number).

OTC inserts have no 加框警語/BBW slot, so `special_warning` is not a valid OTC
field. Rx-only fields (pharmacokinetics, clinical_trials, etc.) are likewise
invalid for OTC; an unknown field name is surfaced in `unknown_fields`.

## How to use this from get_package_insert

Use the same `get_package_insert` tool — the wrapper auto-detects OTC vs Rx from
the insert XML. Any §7+ tail section is returned in `additional_sections` with
its verbatim text. Every response includes `available_sections` (table of
contents) so you can see every populated section without re-fetching.
"""
