; C#.

(method_declaration name: (identifier) @name) @def.function
(constructor_declaration name: (identifier) @name) @def.function
(local_function_statement name: (identifier) @name) @def.function
(property_declaration name: (identifier) @name) @def.field

(class_declaration name: (identifier) @name) @def.class
(record_declaration name: (identifier) @name) @def.class
(struct_declaration name: (identifier) @name) @def.class
(enum_declaration name: (identifier) @name) @def.class
(interface_declaration name: (identifier) @name) @def.interface
(namespace_declaration name: (identifier) @name) @def.module

(field_declaration (variable_declaration (variable_declarator (identifier) @name))) @def.field

(invocation_expression function: (identifier) @name) @ref.call
(invocation_expression
  function: (member_access_expression expression: (_) @recv name: (identifier) @name)) @ref.call
(object_creation_expression type: (identifier) @name) @ref.call

(base_list (identifier) @name) @ref.type

(using_directive (qualified_name) @import.module) @import
(using_directive (identifier) @import.module) @import
