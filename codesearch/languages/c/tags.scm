; C. Also used for .h headers, where the C++ grammar mis-parses plain C more
; often than the C grammar mis-parses simple C++ headers.

(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def.function
(function_definition
  declarator: (pointer_declarator
                declarator: (function_declarator declarator: (identifier) @name))) @def.function
(declaration declarator: (function_declarator declarator: (identifier) @name)) @def.function

(struct_specifier name: (type_identifier) @name) @def.class
(union_specifier name: (type_identifier) @name) @def.class
(enum_specifier name: (type_identifier) @name) @def.class
(type_definition declarator: (type_identifier) @name) @def.field

(field_declaration declarator: (field_identifier) @name) @def.field

(call_expression function: (identifier) @name) @ref.call
(call_expression function: (field_expression field: (field_identifier) @name)) @ref.call

(preproc_include path: (string_literal) @import.module) @import
(preproc_include path: (system_lib_string) @import.module) @import
