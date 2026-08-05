; PHP.

(function_definition name: (name) @name) @def.function
(method_declaration name: (name) @name) @def.function
(class_declaration name: (name) @name) @def.class
(trait_declaration name: (name) @name) @def.class
(enum_declaration name: (name) @name) @def.class
(interface_declaration name: (name) @name) @def.interface
(property_element (variable_name (name) @name)) @def.field
(const_element (name) @name) @def.field

(function_call_expression function: (name) @name) @ref.call
(member_call_expression object: (_) @recv name: (name) @name) @ref.call
(scoped_call_expression scope: (name) @recv name: (name) @name) @ref.call
(object_creation_expression (name) @name) @ref.call

(base_clause (name) @name) @ref.type
(class_interface_clause (name) @name) @ref.type

(namespace_use_declaration (namespace_use_clause (qualified_name) @import.module)) @import
((function_call_expression
   function: (name) @_fn
   arguments: (arguments (argument (string) @import.module))) @import
 (#match? @_fn "^(require|require_once|include|include_once)$"))
