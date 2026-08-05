; JavaScript. Arrow functions bound to a name are treated as declarations —
; in modern JS that is how most functions are actually written, and skipping
; them would leave the call graph almost empty.

(function_declaration name: (identifier) @name) @def.function
(generator_function_declaration name: (identifier) @name) @def.function
(method_definition name: (property_identifier) @name) @def.function
(class_declaration name: (identifier) @name) @def.class
(field_definition property: (property_identifier) @name) @def.field

(variable_declarator name: (identifier) @name value: (arrow_function)) @def.function
(variable_declarator name: (identifier) @name value: (function_expression)) @def.function

; Object literal properties holding a function: the module-object export style.
(pair key: (property_identifier) @name value: (arrow_function)) @def.function
(pair key: (property_identifier) @name value: (function_expression)) @def.function

(call_expression function: (identifier) @name) @ref.call
(call_expression
  function: (member_expression object: (_) @recv property: (property_identifier) @name)) @ref.call
(new_expression constructor: (identifier) @name) @ref.call

(class_heritage (identifier) @name) @ref.type

(import_statement source: (string) @import.module) @import
((call_expression
   function: (identifier) @_fn
   arguments: (arguments (string) @import.module)) @import
 (#eq? @_fn "require"))
