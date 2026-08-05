; Rust.
;
; `impl` blocks are not symbols themselves; the functions inside them are picked
; up by the containment pass and qualified with the type they belong to.

(function_item name: (identifier) @name) @def.function
(function_signature_item name: (identifier) @name) @def.function

(struct_item name: (type_identifier) @name) @def.class
(enum_item name: (type_identifier) @name) @def.class
(union_item name: (type_identifier) @name) @def.class
(trait_item name: (type_identifier) @name) @def.interface
(type_item name: (type_identifier) @name) @def.field
(mod_item name: (identifier) @name) @def.module

(field_declaration name: (field_identifier) @name) @def.field
(const_item name: (identifier) @name) @def.field
(static_item name: (identifier) @name) @def.field

(call_expression function: (identifier) @name) @ref.call
(call_expression function: (field_expression field: (field_identifier) @name)) @ref.call
(call_expression
  function: (scoped_identifier path: (identifier) @recv name: (identifier) @name)) @ref.call
(macro_invocation macro: (identifier) @name) @ref.call

(impl_item trait: (type_identifier) @name) @ref.type

(use_declaration argument: (scoped_identifier) @import.module) @import
(use_declaration argument: (identifier) @import.module) @import
(use_declaration argument: (use_as_clause path: (_) @import.module alias: (identifier) @import.alias)) @import
(extern_crate_declaration name: (identifier) @import.module) @import
