"""Row → API object: the serializer layer (`components/data_model.md` §7 and §8).

`fields.py` holds the field-map machinery every resource shares; `expand.py`
holds the expansion resolver. A resource module's serializer is its `FieldMap`
partially applied to `fields.to_api`.
"""
