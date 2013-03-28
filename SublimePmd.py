from collections import defaultdict, deque
from itertools import cycle
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
SETTINGS = sublime.load_settings("SublimePMD.sublime-settings")

FILL_STYLES = {
    'fill': sublime.DRAW_EMPTY,
    'outline': sublime.DRAW_OUTLINED,
    'none': sublime.HIDDEN
}

messagesByView = defaultdict(list)

problemsLock = threading.RLock()

def getMessage(view):
    messages = messagesByView[view.id()]
    for region, message in messages:
        if region.contains(view.sel()[0]):
            return message


class Edit:
    def __init__(self, view):
        self.view = view


    def __enter__(self):
        self.edit = self.view.begin_edit()
        return self.edit


    def __exit__(self, type, value, traceback):
        self.view.end_edit(self.edit)


class SettingsError(Exception):
    pass


class Runner(threading.Thread):

    def __init__(self, view, settingGetter, results):
        threading.Thread.__init__(self)
        self.view = view
        self.getSetting = settingGetter
        self.results = results


class XLinter(Runner):
    """The logic here was shamelessly ripped from SublimeLinter"""
    ERROR_RE = re.compile(r'^(?P<path>.*\.java):(?P<line>\d+): '
            + r'(?P<warning>warning: )?(?:\[\w+\] )?(?P<error>.*)')
    MARK_RE = re.compile(r'^(?P<mark>\s*)\^$')
    END_RE = re.compile(r'[\d] error')

    def run(self):
        self.filename = self.view.file_name()
        path = ':'.join(self.getSetting('java_classpath') or ['.'])
        
        command = 'javac -g -Xlint -classpath {path} -d {temp} {fname}'.format(
                path = path, fname = self.filename, temp = _TEMP_DIR)

        p = subprocess.Popen(command, shell = True, stderr = subprocess.STDOUT,
                stdout = subprocess.PIPE)
        self._consumeXlintOutput(p)


    def _consumeXlintOutput(self, proc):
        problems = defaultdict(list)
        for line in proc.stdout:
            match = re.match(self.ERROR_RE, line)
            path = ''
            if match:
                path = os.path.abspath(match.group('path'))

                lineNumber = int(match.group('line'))
                warning = WARNING if match.group('warning') else ERROR
                message = match.group('error')

                # Skip forward until we find the marker
                position = -1

                while True:
                    line = proc.stdout.next()
                    match = re.match(self.MARK_RE, line)

                    if match:
                        position = len(match.group('mark'))
                        break

                problems[path].append( dict(level = warning,
                        message = message, sourceLineNumber = lineNumber,
                        sourcePosition = position) )

            elif re.match(self.END_RE, line):
                continue
            elif path and problems[path]:
                problems[path][-1][message] += ('; ' + line.strip())


        for fname, lines in problems.items():
            with problemsLock:
                self.results[fname].extend(lines)
        

class PMDer(Runner):
    def _getPmdRulesets(self):
        rulesetPath = self.getSetting('ruleset_path')
        rules = self.getSetting('rules')
        if rulesetPath:
            return rulesetPath
        elif rules:
            return ','.join('rulesets/java/{0}.xml'.format(r) for r in rules)
        else:
            return self._getPath('example.ruleset.xml')


    def _getPath(self, *args):
        return os.path.join(sublime.packages_path(), 'SublimePMD', *args)


    def run(self):
        fname = self.view.file_name()
        rulesets = self._getPmdRulesets()

        classpath = ':'.join([ self._getPath('pmd-bin-5.0.0', 'lib', f) 
                for f in os.listdir(self._getPath('pmd-bin-5.0.0', 'lib'))
                if f.endswith('.jar')])

        cmd = ['java', '-classpath', classpath, 
                'net.sourceforge.pmd.PMD', fname, 'text', rulesets]
        sub = subprocess.Popen(cmd,
                stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
        self._consumePmdOutput(sub)


    def _consumePmdOutput(self, proc):
        for line in proc.stdout:
            try:
                fname, line = line.split(':', 1)
                lineNumber, message = line.split('\t', 1)
                with problemsLock:
                    self.results[fname].append( dict(level = WARNING, 
                            sourceLineNumber = int(lineNumber),
                            message = message.strip(),
                            sourcePosition = 0) )
            except ValueError:
                print 'error on line: %s' % line


class PmdCommand(sublime_plugin.TextCommand):
    problems = defaultdict(deque)
    problems__doc = """problems is a deque of dicts.  each entry is a key of 
            filename to a list of dicts.  each dict in that list represents a 
            problem found in a source file, and has these mandatory keys:
            
            level -- one of WARNING or ERROR
            message -- string; the message from either XLint or PMD
            sourceLineNumber -- int; the line number where the problem was found
            soucePosition -- int; the column number where the problem was found

            And these optional key:

            sourceLine -- string; the line from the source file where the 
                    problem was found
            """


    def getSetting(self, name):
        settings = sublime.active_window().active_view().settings()
        if settings.has(name):
            return settings.get(name)
            
        if SETTINGS.has(name):
            return SETTINGS.get(name)

        return None


    def run(self, current_file = False, *args):
        print current_file
        print args
        # clear old display
        for level in [ERROR, WARNING]:
            self.view.erase_regions(level)

        # get set up
        self.window = self.view.window()
        self.problems.clear()
        self.getSetting('')

        # run in new thread
        threading.Thread(target = self._run).start()


    def _run(self):
        self.startSpinner()

        runners = []
        if self.getSetting('do_xlint'):
            runners.append(XLinter(self.view, self.getSetting, self.problems))
        if self.getSetting('do_pmd'):
            runners.append(PMDer(self.view, self.getSetting, self.problems))

        for t in runners:
            t.start()

        while runners:
            runners.pop(0).join()

        self.stopSpinner()

        self._printProblems()


    def startSpinner(self):
        out = self._getResultsPane('PMD Results')
        with Edit(out) as edit:
            out.erase(edit, sublime.Region(0, out.size()))
            self._append(out, edit, self.view.file_name() + ":\n\n")

        chars = cycle(['[  ]', '[- ]', '[--]', '[ -]'])

        def spin():
            if not self.keepSpinning:
                return
            
            with Edit(out) as edit:
                line = out.line(out.text_point(2, 0))
                out.replace(edit, line, chars.next())
            
            sublime.set_timeout(spin, 200)

        self.keepSpinning = True
        sublime.set_timeout(spin, 0)


    def stopSpinner(self):
        self.keepSpinning = False
        

    def _printProblems(self):
        messagesForOutPane = []
        regions = defaultdict(list)
        while messagesByView[self.view.id()]:
            messagesByView[self.view.id()].pop(0)

        for filename, problems in sorted(self.problems.items(), 
                key = lambda x: x[0]):

            for problem in sorted(problems, 
                    key = lambda x: x['sourceLineNumber']):
                point = self.view.text_point(problem['sourceLineNumber'] - 1, 
                        problem['sourcePosition'] + 1)
                line = self.view.line(point)
                region = (line if problem['sourcePosition'] == 0 
                        else self.view.word(point))
                problem['sourceLine'] = self.view.substr(line)

                if filename == self.view.file_name():
                    messagesForOutPane.append(problem)

                    messagesByView[self.view.id()].append( (region, 
                            problem['message']) )
                    regions[problem['level']].append(region)

            
        if regions and self.getSetting('highlight'):
            mark = 'circle' if self.getSetting("gutter_marks") else ''
            style = FILL_STYLES.get(
                    self.getSetting('highlight_style'), 'outline')
            for level, errs in regions.iteritems():
                self.view.add_regions(level, errs, level, 
                        mark, style)
                time.sleep(.100)

        if self.getSetting('results_pane'):
            out = self._getResultsPane('PMD Results')
            with Edit(out) as edit:
                out.replace(edit, sublime.Region(0, out.size()),
                        self.view.file_name() + ":\n\n")
                outPaneMarks = defaultdict(list)
                for problem in messagesForOutPane:
                    start = out.size()
                    size = out.insert(edit, start, 
                            self._formatMessage(problem))
                    out.insert(edit, out.size(), '\n')
                    outPaneMarks[problem['level']].append(
                        sublime.Region(start + 1, start + 1 + size))

                for level, regions in outPaneMarks.items():
                    print regions
                    out.add_regions(level, regions, level, 'dot',
                        sublime.HIDDEN)
                    time.sleep(0.1)

                if not messagesForOutPane:
                    self._append(out, edit, '       -- pass -- ')


    def _formatMessage(self, problem): 
        line = problem['sourceLine']
        lineNumber = problem['sourceLineNumber']
        if len(line) > 80:
            line = line[:77] + '...'
        spacer1 = ' ' * (5 - len(str(lineNumber)))
        spacer2 = ' ' * (81 - len(line))
        
        return '{sp1}{lineNumber}: {text}{sp2}{message}'.format(
                lineNumber = lineNumber, text = line, sp1 = spacer1,
                sp2 = spacer2, message = problem['message'])


    def _raiseOutputPane(self, outputPane, basePane):
        if (basePane.window().active_view().id() == basePane.id()):
            self.window.focus_view(outputPane)
            self.window.focus_view(basePane)


    def _getResultsPane(self, name):
        resultsPane = [v for v in self.window.views() 
                if v.name() == name]
        if resultsPane:
            v = resultsPane[0]
            sublime.set_timeout(lambda: self._raiseOutputPane(v, self.view), 0)
            return v

        # otherwise, create a new view, and name it 'PMD Results'
        results = self.window.new_file()
        results.set_name(name)
        results.settings().set('syntax', os.path.join(
                'Packages', 'Default', 'Find Results.hidden-tmLanguage'))
        results.settings().set('rulers', [6, 86])
        results.settings().set('draw_indent_guides', False)
        results.set_scratch(True)
        return results


    def _append(self, view, edit, text, newline = True):
        def _actuallyAppend():
            view.insert(edit, view.size(), text)
            if newline:
                view.insert(edit, view.size(), '\n')
        sublime.set_timeout(_actuallyAppend, 0)


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