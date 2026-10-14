{#
  Default dbt would prefix custom schemas like "financebuddy_marts".
  We want clean names: bronze / staging / marts — especially on Athena where
  those become separate Glue databases.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}