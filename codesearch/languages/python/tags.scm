; Python. Methods are not matched separately — a function_definition nested in a
; class_definition is promoted to a method by the containment pass, which keeps
; every language pack down to one rule per concept.

(function_definition name: (identifier) @name) @def.function
(class_definition name: (identifier) @name) @def.class

; Module-level assignments are globals worth tracking; locals are not.
;
; The capture sits on the assignment, not on `module`. Attaching it to the outer
; pattern would give every global a span covering the whole file, which makes it
; the containment parent of every definition after it — one wrong capture
; position silently corrupts every qualified name in the language.
(module (expression_statement (assignment left: (identifier) @name) @def.field))

(call function: (identifier) @name) @ref.call
(call function: (attribute object: (_) @recv attribute: (identifier) @name)) @ref.call

(class_definition superclasses: (argument_list (identifier) @name)) @ref.type
(class_definition
  superclasses: (argument_list (attribute attribute: (identifier) @name))) @ref.type

(import_statement name: (dotted_name) @import.module) @import
(import_statement
  name: (aliased_import
          name: (dotted_name) @import.module
          alias: (identifier) @import.alias)) @import
(import_from_statement
  module_name: (dotted_name) @import.module
  name: (dotted_name) @import.symbol) @import
(import_from_statement
  module_name: (relative_import) @import.module
  name: (dotted_name) @import.symbol) @import
