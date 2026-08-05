; C++. Builds on C's shapes and adds classes, namespaces and qualified names.

(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def.function
(function_definition
  declarator: (function_declarator declarator: (field_identifier) @name)) @def.function
(function_definition
  declarator: (function_declarator
                declarator: (qualified_identifier name: (identifier) @name))) @def.function
(declaration declarator: (function_declarator declarator: (identifier) @name)) @def.function
(field_declaration declarator: (function_declarator declarator: (field_identifier) @name)) @def.function

(class_specifier name: (type_identifier) @name) @def.class
(struct_specifier name: (type_identifier) @name) @def.class
(union_specifier name: (type_identifier) @name) @def.class
(enum_specifier name: (type_identifier) @name) @def.class
(namespace_definition name: (namespace_identifier) @name) @def.module

(field_declaration declarator: (field_identifier) @name) @def.field

(call_expression function: (identifier) @name) @ref.call
(call_expression function: (field_expression field: (field_identifier) @name)) @ref.call
(call_expression
  function: (qualified_identifier scope: (namespace_identifier) @recv name: (identifier) @name)) @ref.call

(base_class_clause (type_identifier) @name) @ref.type

(preproc_include path: (string_literal) @import.module) @import
(preproc_include path: (system_lib_string) @import.module) @import
