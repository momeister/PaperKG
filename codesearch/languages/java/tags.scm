; Java.

(method_declaration name: (identifier) @name) @def.function
(constructor_declaration name: (identifier) @name) @def.function

(class_declaration name: (identifier) @name) @def.class
(record_declaration name: (identifier) @name) @def.class
(enum_declaration name: (identifier) @name) @def.class
(interface_declaration name: (identifier) @name) @def.interface

(field_declaration (variable_declarator name: (identifier) @name)) @def.field

(method_invocation name: (identifier) @name) @ref.call
(method_invocation object: (identifier) @recv name: (identifier) @name) @ref.call
(object_creation_expression type: (type_identifier) @name) @ref.call

(superclass (type_identifier) @name) @ref.type
(super_interfaces (type_list (type_identifier) @name)) @ref.type

(import_declaration (scoped_identifier) @import.module) @import
