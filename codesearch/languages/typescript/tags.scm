; TypeScript, shared by the .ts and .tsx grammars.
;
; Node names differ from plain JavaScript in ways that are easy to miss —
; `public_field_definition` rather than `field_definition`, an explicit
; `extends_clause` rather than a bare identifier under `class_heritage`. A single
; wrong name makes the whole query fail to compile, which is why
; `every_language_pack_compiles` in registry.rs exists.

(function_declaration name: (identifier) @name) @def.function
(generator_function_declaration name: (identifier) @name) @def.function
(function_signature name: (identifier) @name) @def.function
(method_definition name: (property_identifier) @name) @def.function
(method_signature name: (property_identifier) @name) @def.function
(abstract_method_signature name: (property_identifier) @name) @def.function

(class_declaration name: (type_identifier) @name) @def.class
(abstract_class_declaration name: (type_identifier) @name) @def.class
(interface_declaration name: (type_identifier) @name) @def.interface
(enum_declaration name: (identifier) @name) @def.class
(type_alias_declaration name: (type_identifier) @name) @def.field

(public_field_definition name: (property_identifier) @name) @def.field
(property_signature name: (property_identifier) @name) @def.field

(variable_declarator name: (identifier) @name value: (arrow_function)) @def.function
(variable_declarator name: (identifier) @name value: (function_expression)) @def.function
(pair key: (property_identifier) @name value: (arrow_function)) @def.function

(call_expression function: (identifier) @name) @ref.call
(call_expression
  function: (member_expression object: (_) @recv property: (property_identifier) @name)) @ref.call
(new_expression constructor: (identifier) @name) @ref.call

(extends_clause value: (identifier) @name) @ref.type
(extends_type_clause type: (type_identifier) @name) @ref.type
(implements_clause (type_identifier) @name) @ref.type

(import_statement source: (string) @import.module) @import
((call_expression
   function: (identifier) @_fn
   arguments: (arguments (string) @import.module)) @import
 (#eq? @_fn "require"))
