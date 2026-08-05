; Ruby.

(method name: (identifier) @name) @def.function
(singleton_method name: (identifier) @name) @def.function
(class name: (constant) @name) @def.class
(module name: (constant) @name) @def.module

(call method: (identifier) @name) @ref.call
(call receiver: (_) @recv method: (identifier) @name) @ref.call

(class superclass: (superclass (constant) @name)) @ref.type

; `require`, `require_relative` and `include` are all import-shaped.
((call
   method: (identifier) @_fn
   arguments: (argument_list (string) @import.module)) @import
 (#match? @_fn "^(require|require_relative|load|autoload)$"))
