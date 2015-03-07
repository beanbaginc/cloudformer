from __future__ import unicode_literals

import re

from yaml.constructor import ConstructorError

from cloudformer.templates.state import (UncollapsibleList, VarReference,
                                         VarsStringsList)


class StringParserStack(list):
    """Manages the stack of functions and other items when parsing strings.

    This is a convenience around a list that associates the stack with
    any added items, provides access to the StringParser, and provides
    friendlier access to the most recent stack item.
    """

    def __init__(self, parser):
        super(StringParserStack, self).__init__()

        self.parser = parser

    @property
    def current(self):
        """Return the current stack item."""
        return self[-1]

    def push(self, item):
        """Push a new item onto the stack.

        The item will have its ``stack`` attribute set to this stack.
        """
        item.stack = self
        self.append(item)


class StringParserStackItem(object):
    """A parsed item from a string that can appear on the stack.

    The item may have content, and may indicate how many pops are required
    to clear back to the parent. Content can be added, and the representation
    of this item can be serialized.

    Subclasses can provide additional functionality and serialization.
    """

    def __init__(self, stack=None, contents=None, pop_count=1):
        self.stack = stack
        self.contents = contents or []
        self.pop_count = pop_count

    def add_content(self, content):
        """Add content to the item.

        This will return True if the added content can be pushed as a new
        item onto the stack.
        """
        self.contents.append(content)

        return isinstance(content, Function)

    def serialize(self):
        """Serialize the stack item and its contents to a data structure."""
        return [
            self.normalize_content(content)
            for content in self.contents
        ]

    def normalize_content(self, content):
        """Normalize the provided content.

        If the content is another stack item, it will be serialized.

        If it's a list, it will be normalized, collapsed, and set up with
        a Fn::Join if appropriate.
        """
        if isinstance(content, StringParserStackItem):
            content = content.serialize()

        if isinstance(content, list):
            content = [
                self.normalize_content(c)
                for c in content
            ]

            template_state = self.stack.parser.template_state
            content = template_state.normalize_vars_list(
                template_state.process_tree(content, resolve_variables=False))

            if len(content) == 1:
                content = content[0]
            elif len(content) > 1 and not isinstance(content, VarsStringsList):
                content = {
                    'Fn::Join': ['', content]
                }

        return content

    def __repr__(self):
        return '<%r: %r>' % (self.__class__.__name__, self.serialize())


class Function(StringParserStackItem):
    """A CloudFormation function call appearing in a string."""

    def __init__(self, func_name, params=None, **kwargs):
        super(Function, self).__init__(**kwargs)

        self.func_name = func_name
        self.params = params or []

    def validate(self, stack):
        """Validates the function's placement in the current stack."""
        pass

    def normalize_function_name(self):
        """Normalize the function name used for serialization.

        By default, this prefixes the function name with "Fn::", as
        needed by CloudFormation.
        """
        return 'Fn::%s' % self.func_name

    def serialize(self):
        norm_func_name = 'Fn::%s' % self.func_name

        return {
            norm_func_name: self.params,
        }


class BlockFunction(Function):
    """A CloudFormation block-level function call appearing in a string."""

    def normalize_function_contents(self, contents):
        """Normalize the block contents of the function.

        By default, this called normalize_content() on each piece of
        content.
        """
        return [
            self.normalize_content(content)
            for content in contents
        ]

    def serialize(self):
        norm_func_name = self.normalize_function_name()
        norm_contents = self.normalize_function_contents(self.contents)

        return {
            norm_func_name: UncollapsibleList(self.params + norm_contents)
        }


class IfBlockFunction(BlockFunction):
    """A CloudFormation If statement.

    If statements, in our template, can have matching Else and ElseIf
    statements. These all get turned into a tree of CloudFormation If
    statements.
    """

    def __init__(self, *args, **kwargs):
        super(IfBlockFunction, self).__init__(*args, **kwargs)

        self._is_elseif = False
        self._if_true_content = []
        self._if_false_content = []
        self._cur_content = self._if_true_content

    def validate(self, stack):
        """Validate the If statement's position in the stack.

        If this is actually an ElseIf, then this will ensure it's placed
        in an If statement.
        """
        if self._is_elseif and not isinstance(stack.current, IfBlockFunction):
            raise ConstructorError(
                'Found ElseIf without a matching If or ElseIf')

    def add_content(self, content):
        """Add content to the if statement.

        Initially, content will be added to the if-true section.
        If an Else or ElseIf function is being added, content will
        switch to being added to the if-false section.

        Adding an ElseIf will trigger a new If block inside the if-false
        section.
        """
        if isinstance(content, BlockFunction):
            if content.func_name in ('Else', 'ElseIf'):
                if not self._if_true_content:
                    raise ConstructorError(
                        'Found %s without a "true" value in the If'
                        % content.func_name)
                elif self._if_false_content:
                    raise ConstructorError(
                        'Found %s after an Else'
                        % content.func_name)

                self._cur_content = self._if_false_content

                if content.func_name == 'Else':
                    return False
                elif content.func_name == 'ElseIf':
                    # Simulate being an If statement, since that's what
                    # it turns into. Set it up to handle the proper depth
                    # in the stack.
                    content.func_name = 'If'
                    content.pop_count = self.pop_count + 1
                    content._is_elseif = True

        self._cur_content.append(content)

        return True

    def normalize_function_name(self):
        """Normalize the name of the function.

        An If and ElseIf will always be serialized to Fn::If in
        CloudFormation.
        """
        return 'Fn::If'

    def normalize_function_contents(self, contents):
        """Normalizes the if-true and if-false content.

        The if-true content will always be added, but if-false will only
        be added if there's actual content there.
        """
        contents = []
        if_true_content = self.normalize_content(self._if_true_content)
        if_false_content = self.normalize_content(self._if_false_content)

        if isinstance(if_true_content, list):
            contents += if_true_content
        else:
            contents.append(if_true_content)

        if if_false_content:
            if isinstance(if_false_content, list):
                contents += if_false_content
            else:
                contents.append(if_false_content)

        return contents


class ElseBlockFunction(BlockFunction):
    """An Else block, as part of an If statement."""

    def validate(self, stack):
        """Validate the Else block's position in hte stack.

        This will ensure that the Else block is within an If block.
        """
        if not isinstance(stack.current, IfBlockFunction):
            raise ConstructorError('Found Else without a matching If')


class StringParser(object):
    """Parses a string for functions, variables, and references."""

    PARSE_STR_RE = re.compile(
        r'('

        # CloudFormation functions, optionally with opening blocks
        r'<%\s*(?P<func_name>[A-Za-z]+)\s*'
        r'(\((?P<params>([^)]+))\))?'
        r'(?P<func_open>\s*{)?\s*%>\n?'

        # Closing braces for block-level CloudFormation functions
        r'|(?P<func_close><%\s*}\s*%>)\n?'

        # Resource/Parameter references
        r'|@@(?P<ref_brace>{)?(?P<ref_name>[A-Za-z0-9:_]+)(?(ref_brace)})'

        # Template variables
        r'|\$\$((?P<var_name>[A-Za-z0-9_]+)|{(?P<var_path>[A-Za-z0-9_.]+)})'

        r')')

    FUNC_PARAM_RE = re.compile(',\s*')

    FUNCTIONS = {
        'If': IfBlockFunction,
        'ElseIf': IfBlockFunction,
        'Else': ElseBlockFunction,
    }

    def __init__(self, template_state):
        self.template_state = template_state

    def parse_string(self, s):
        """Parse a string.

        The string will be parsed, with variables, function calls, and
        references being turned into their appropriate CloudFormation
        representations.

        The resulting string, or list of strings/dictionaries, will be
        returned.

        If the result is a list of items, they will be wrapped in a Fn::Join.

        If the string starts with "__base64__", the result will be wrapped
        in a Fn::Base64.
        """
        lines = s.splitlines(True)

        if lines[0].strip() == '__base64__':
            process_func = 'Fn::Base64'
            lines = lines[1:]
        else:
            process_func = None

        func_stack = StringParserStack(self)

        # Parse the line, factoring in the previous lines' stack-altering
        # function calls, to build a single stack of all strings and
        # functions.
        for line in lines:
            self._parse_line(line, func_stack)

        # Make sure we have a completed stack without any missing
        # end blocks.
        if len(func_stack) > 1:
            raise ConstructorError('Unbalanced braces in template')

        cur_stack = func_stack.current
        result = cur_stack.normalize_content(cur_stack)

        if process_func:
            result = {
                process_func: result,
            }

        return result

    def _parse_line(self, s, func_stack=None):
        """Parse a line for any references, functions, or variables.

        Any substrings starting with "@@" will be turned into a
        { "Ref": "<name>" } mapping.

        Any substrings contained within "<% ... %>" will be turned into a
        { "Fn::<name>": { ... } } mapping.

        Any substrings starting with "$$" will be resolved into a variable's
        content, if the variable exists, or a VarReference if not.

        The provided function stack will be updated based on the results
        of the parse.
        """
        prev = 0

        if func_stack is None:
            stack = StringParserStack(self)
        else:
            stack = func_stack

        if not stack:
            stack.push(StringParserStackItem())

        for m in self.PARSE_STR_RE.finditer(s):
            start = m.start()
            groups = m.groupdict()

            if start > 0:
                stack.current.add_content(s[prev:start])

            if groups['func_name']:
                self._handle_func(groups, stack)
            elif groups['func_close']:
                self._handle_func_block_close(stack)
            elif groups['ref_name']:
                self._handle_ref_name(groups, stack)
            elif groups['var_name']:
                self._handle_var(stack, groups['var_name'])
            elif groups['var_path']:
                self._handle_var(stack, groups['var_path'])

            prev = m.end()

        if prev != len(s):
            stack.current.add_content(s[prev:])

        if func_stack is not None:
            return None
        else:
            parts = stack.current.contents

            if len(parts) > 1:
                return self.template_state.collapse_variables(parts)
            else:
                return parts[0]

    def _handle_func(self, groups, stack):
        """Handles functions found in a line.

        The list of parameters to the function will be parsed, and a
        Function or similar subclass will be instantiated with the
        information from the function.
        """
        func_name = groups['func_name']
        params = groups['params']
        cur_stack = stack.current

        if params:
            norm_params = [
                self._parse_line(value)
                for value in self.FUNC_PARAM_RE.split(params)
            ]
        else:
            norm_params = []

        cls = self.FUNCTIONS.get(func_name, BlockFunction)
        func = cls(func_name, norm_params, stack=stack)
        func.validate(stack)

        can_push = cur_stack.add_content(func)

        if can_push and groups['func_open']:
            stack.push(func)

    def _handle_func_block_close(self, stack):
        """Handles the end of block functions found in a line."""
        for i in range(stack.current.pop_count):
            stack.pop()

    def _handle_ref_name(self, groups, stack):
        """Handles resource references found in a line."""
        stack.current.add_content({
            'Ref': groups['ref_name']
        })

    def _handle_var(self, stack, var_name):
        """Handles variable references found in a line."""
        stack.current.add_content(VarReference(var_name))