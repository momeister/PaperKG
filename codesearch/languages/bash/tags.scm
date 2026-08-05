; Bash. Shell scripts have no classes and no import system, but the function
; graph is exactly what makes a long build or deploy script comprehensible —
; which is often the script you most need explained.

(function_definition name: (word) @name) @def.function
(variable_assignment name: (variable_name) @name) @def.field

(command name: (command_name (word) @name)) @ref.call

((command
   name: (command_name (word) @_fn)
   argument: (word) @import.module) @import
 (#match? @_fn "^(source|\\.)$"))
