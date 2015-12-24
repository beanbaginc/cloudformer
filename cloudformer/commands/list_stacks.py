from __future__ import print_function, unicode_literals

import json
import os
import sys
from datetime import datetime

from colorama import Fore, Style

from cloudformer.cloudformation import CloudFormation
from cloudformer.commands import BaseCommand, run_command
from cloudformer.errors import StackCreationError
from cloudformer.templates import TemplateCompiler
from cloudformer.utils.console import prompt_template_param


class ListStacks(BaseCommand):
    """Lists all stacks and their outputs in CloudFormation."""

    def add_options(self, parser):
        parser.add_argument(
            '--region',
            default='us-east-1',
            help='The region to connect to.')
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Output as JSON.')

    def main(self):
        cf = CloudFormation(self.options.region)

        if self.options.json:
            self._print_stacks_json(cf)
        else:
            self._print_stacks(cf)

    def _print_stacks_json(self, cf):
        """Print the list of stacks as pretty-printed JSON."""
        print(json.dumps(
            [
                {
                    'name': stack.stack_name,
                    'status': stack.stack_status,
                    'description': stack.description,
                    'arn': stack.stack_id,
                    'created': stack.creation_time.isoformat(),
                    'outputs': dict(
                        (output.key, output.value)
                        for output in stack.outputs
                    ),
                }
                for stack in cf.lookup_stacks()
            ],
            indent=2))

    def _print_stacks(self, cf):
        """Print the list of stacks as formatted console output."""
        first = True

        for stack in cf.lookup_stacks():
            if first:
                first = False
            else:
                print()
                print()

            if stack.stack_status.endswith('FAILED'):
                status_color = Fore.RED
            elif stack.stack_status.endswith('COMPLETE'):
                status_color = Fore.GREEN
            elif stack.stack_status.endswith('PROGRESS'):
                status_color = Fore.YELLOW

            self._print_field(stack.stack_name, key_color=Fore.CYAN)
            self._print_field('Status', stack.stack_status, indent_level=1,
                              value_color=status_color)
            self._print_field('Description', stack.description, indent_level=1)
            self._print_field('ARN', stack.stack_id, indent_level=1)
            self._print_field('Created', stack.creation_time, indent_level=1)

            if stack.outputs:
                self._print_field('Outputs', indent_level=1)

                for output in stack.outputs:
                    self._print_field(output.key, output.value, indent_level=2)

    def _print_field(self, key, value='', indent_level=0, key_color=None,
                     value_color=None):
        """Print a key/value field to the console.

        The keys will be printed as bold. This has several additional options
        for controlling presentation.

        Args:
            key (unicode):
                The key to print.

            value (unicode, optional):
                The value to print.

            indent_level (int, optional):
                The indentation level. Each level adds 4 spaces before the key.

            key_color (unicode, optional):
                The color code to use for the key.

            value_color (unicode, optional):
                The color code to use for the value.
        """
        if key_color:
            key = '%s%s%s' % (key_color, key, Style.RESET_ALL)

        if value and value_color:
            value = '%s%s%s' % (value_color, value, Style.RESET_ALL)

        s = '%s%s%s:%s' % (indent_level * '    ', Style.BRIGHT, key,
                           Style.RESET_ALL)

        if value:
            s += ' %s' % value

        print(s)


def main():
    run_command(ListStacks)