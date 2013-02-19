from collections import defaultdict, deque
import os
import re
import subprocess
import tempfile
import threading
import time

import sublime
import sublime_plugin

_TEMP_DIR = tempfile.mkdtemp()

ERROR = 'sublimePMD.error'
WARNING = 'sublimePMD.warning'

FILL_STYLES = {
    'fill': sublime.DRAW_EMPTY,
    'outline': sublime.DRAW_OUTLINED,
    'none': sublime.HIDDEN
}

messagesByView = defaultdict(list)

def getMessage(view):
    messages = messagesByView[view.id()]
    for region, message in messages:
        if region.contains(view.sel()[0]):
            return message

class SettingsError(Exception):
    pass


class XLintParser:
    """The logic here was shamelessly ripped from SublimeLinter"""
    ERROR_RE = re.compile(r'^(?P<path>.*\.java):(?P<line>\d+): '
            + r'(?P<warning>warning: )?(?:\[\w+\] )?(?P<error>.*)')
    MARK_RE = re.compile(r'^(?P<mark>\s*)\^$')


    def __init__(self, filename):
        self.filename = filename


    def parse(self, lines):
        problems = []

        for line in lines:
            match = re.match(self.ERROR_RE, line)
            if match:
                path = os.path.abspath(match.group('path'))

                if path != self.filename:
                    continue

                lineNumber = int(match.group('line'))
                warning = WARNING if match.group('warning') else ERROR
                message = match.group('error')

                # Skip forward until we find the marker
                position = -1

                while True:
                    line = lines.next()
                    match = re.match(self.MARK_RE, line)

                    if match:
                        position = len(match.group('mark'))
                        break

                problems.append( (lineNumber, position, warning, message) )

        return problems


SETTINGS = sublime.load_settings("SublimePMD.sublime-settings")


class PmdCommand(sublime_plugin.TextCommand):
    problems = deque()

    def getSetting(self, name):
        if SETTINGS.has(name):
            return SETTINGS.get(name)

        settings = sublime.active_window().active_view().settings()
        if settings.has(name):
            return settings.get(name)
        return None


    def run(self, *args):
        self.window = self.view.window()
        self.problems.clear()
        threads = []
        if self.getSetting('do_xlint'):
            threads.append(threading.Thread(target = self._doXLint))
        if self.getSetting('do_pmd'):
            threads.append(threading.Thread(target = self._doPmd))

        for t in threads:
            t.start()

        while threads:
            threads.pop(0).join()

        self._printProblems()


    def _printProblems(self):
        for level in [ERROR, WARNING]:
            self.view.erase_regions(level)

        messagesForOutPane = []
        regions = defaultdict(list)
        while messagesByView[self.view.id()]:
            messagesByView[self.view.id()].pop(0)
        for lnumber, position, level, message in sorted(self.problems, 
                key = lambda x: x[0]):
            point = self.view.text_point(lnumber - 1, position + 1)
            line = self.view.line(point)
            region = line if position == 0 else self.view.word(point)

            messagesForOutPane.append(self._formatMessage(lnumber, 
                    self.view.substr(line), message))
            messagesByView[self.view.id()].append( (region, message) )
            regions[level].append(region)

            
        if regions and self.getSetting('highlight'):
            mark = 'circle' if self.getSetting("gutter_marks") else ''
            style = FILL_STYLES.get(
                    self.getSetting('highlight_style'), 'outline')
            for level, errs in regions.iteritems():
                self.view.add_regions(level, errs, level, 
                        mark, style)
                time.sleep(.100)

        if self.getSetting('results_pane'):
            out = self._getResultsPane(self.view)
            edit = out.begin_edit()
            try:
                out.erase(edit, sublime.Region(0, out.size()))
                self._append(out, edit, self.view.file_name() + ":\n")
                if messagesForOutPane:
                    print 'appending %s pmd errors' % len(messagesForOutPane)
                    self._append(out, edit, '\n'.join(messagesForOutPane))
                else:
                    self._append(out, edit, '       -- pass -- ')

            finally:
                out.end_edit(edit)


    def _formatMessage(self, lineNumber, line, message):
        # lineNumber += 1

        if len(line) > 80:
            line = line[:77] + '...'
        spacer1 = ' ' * (4 - len(str(lineNumber)))
        spacer2 = ' ' * (81 - len(line))
        
        return '{sp1}{lineNumber}: {text}{sp2}{message}'.format(
                lineNumber = lineNumber, text = line, sp1 = spacer1,
                sp2 = spacer2, message = message)


    def _getPmdRulesets(self):
        rulesetPath = self.getSetting('ruleset_path')
        rules = self.getSetting('rules')
        if rulesetPath:
            return rulesetPath
        elif rules:
            return ','.join('rulesets/java/{0}.xml'.format(r) for r in rules)
        else:
            raise SettingsError('Must specify either "ruleset_path" or "rules" '
                    + 'in your settings.')


    def _doPmd(self):
        # lnumberToErrors = defaultdict(list)

        fname = self.view.file_name()
        rulesets = self._getPmdRulesets()
        script = os.path.join(sublime.packages_path(), 'SublimePMD', 
                'pmd-bin-5.0.0', 'bin', 'run.sh')
        cmd = [script, 'pmd', fname, 'text', rulesets]
        sub = subprocess.Popen(' '.join(cmd), shell = True, 
                stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
        self._consumePmdOutput(sub)


    def _consumePmdOutput(self, proc):
        for line in proc.stdout:
            try:
                fname, line = line.split(':', 1)
                lineNumber, message = line.split('\t', 1)
                self.problems.append( 
                        (int(lineNumber), 0, WARNING, message.strip()) )
            except ValueError:
                print 'error on line: %s' % line
            

    def _getInfoFromStdOutLine(self, line, fileName):
        if line[:len(fileName)] == fileName:
            trimmed = line[len(fileName) + 1:]
            lineNumber = trimmed.split()[0]
            err = trimmed[len(str(lineNumber)):].lstrip()
            return int(lineNumber), err
        else:
            return 0, line
    

    def _getResultsPane(self, view):
        resultsPane = [v for v in self.window.views() 
                if v.name() == 'PMD Results']
        if resultsPane:
            v = resultsPane[0]
            self.window.focus_view(v)
            self.window.focus_view(view)
            return v

        # otherwise, create a new view, and name it 'PMD Results'
        results = self.window.new_file()
        results.set_name('PMD Results')
        results.settings().set('syntax', os.path.join(
                'Packages', 'Default', 'Find Results.hidden-tmLanguage'))
        results.settings().set('rulers', [6, 86])
        results.set_scratch(True)
        return results


    def _append(self, view, edit, text, newline = True):
        def _actuallyAppend():
            view.insert(edit, view.size(), text)
            if newline:
                view.insert(edit, view.size(), '\n')
        sublime.set_timeout(_actuallyAppend, 0)


    def _doXLint(self):
        fname = self.view.file_name()
        path = ':'.join(self.getSetting('java_classpath') or [])
        
        command = 'javac -Xlint -classpath {path} -d {temp} {fname}'.format(
                path = path, fname = fname, temp = _TEMP_DIR)

        p = subprocess.Popen(command, shell = True, stderr = subprocess.STDOUT,
                stdout = subprocess.PIPE)
        self._consumeXlintOutput(p)


    def _consumeXlintOutput(self, proc):
        parser = XLintParser(self.view.file_name())
        problems = parser.parse(proc.stdout)

        for problem in problems:
            self.problems.append(problem)



class SublimePMDBackground(sublime_plugin.EventListener):
    
    def on_post_save(self, view):
        if (view.settings().get('syntax')[-15:] == 'Java.tmLanguage'
                and view.settings().get('pmd_on_save', True)):
            view.run_command('pmd')

    def on_selection_modified(self, view):
        message = getMessage(view)
        if message:
            view.set_status('sublimePMD-tip', message)
        else:
            view.erase_status('sublimePMD-tip')