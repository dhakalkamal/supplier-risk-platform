-- Custom test: every row in supplier_feature_vector must map to a known supplier.
--
-- An orphan row means a supplier_id appeared in a feature mart but was never
-- registered in dim_suppliers. This should be impossible given the join structure,
-- but guards against data races where a supplier is scored before it is fully resolved.
-- The test passes when this query returns zero rows.

select
    fv.supplier_id
from {{ ref('supplier_feature_vector') }} fv
left join {{ ref('dim_suppliers') }} d on fv.supplier_id = d.supplier_id
where d.supplier_id is null
