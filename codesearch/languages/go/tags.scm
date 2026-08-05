; Go.

(function_declaration name: (identifier) @name) @def.function
(method_declaration name: (field_identifier) @name) @def.function

(type_declaration
  (type_spec name: (type_identifier) @name type: (struct_type))) @def.class
(type_declaration
  (type_spec name: (type_identifier) @name type: (interface_type))) @def.interface

; Captures sit on the spec, not on the declaration: a grouped `const ( ... )`
; block would otherwise give every constant in it the span of the whole block.
(field_declaration name: (field_identifier) @name) @def.field
(const_declaration (const_spec name: (identifier) @name) @def.field)
(var_declaration (var_spec name: (identifier) @name) @def.field)

(call_expression function: (identifier) @name) @ref.call
(call_expression
  function: (selector_expression operand: (_) @recv field: (field_identifier) @name)) @ref.call

; Struct embedding is Go's inheritance.
(field_declaration type: (type_identifier) @name) @ref.type

(import_spec path: (interpreted_string_literal) @import.module) @import
(import_spec
  name: (package_identifier) @import.alias
  path: (interpreted_string_literal) @import.module) @import
