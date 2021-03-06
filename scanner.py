import sqlite3
import datetime
import util
import os
import re
import resource
import astroid

'''
scanner interface is: 'scanDirectory(repoPath, reportPath, UniqueSourceID, sourceJSON)'
    repoPath is a the directory where the repo to scan exists
    reportPath is where sqlite can open a connection to the report DB
    UniqueSourceID is unique across all sourcers to this project source
    sourceJSON holds whatever information the sourcer provides to clarify the type, meta and data information about the source - like it was Github, ropID 15341, master branch, clone_url..., sha 2344833 committed at time 14393383838.  These details can depend on what is useful, and are likely to evolve a bit over time.
'''


class PythonRegexScanner:
    id = "python regex scanner"
    pythonFilter = re.compile('.*\.py$', re.IGNORECASE)

    def __init__(self, logger):
        self.logger = logger

    def scanDirectory(self, path, report_db, uniqueSourceID, sourceJSON, shaSet, filePathSet, citationSet):
        scanBeginS = util.getDateTimeS(datetime.datetime.utcnow())
        self.logger.debug("PyReS - scanDirectory, path: " + str(path) + " uniqueSourceID: " + str(uniqueSourceID) + " scanBeginS: " + str(scanBeginS) + " report_db: " + report_db + " sourceJSON: " + sourceJSON)

        # get a list of the python file paths
        allFiles = []
        for root, dirs, files in os.walk(path):
            for name in files:
                allFiles.append(os.path.join(root, name))
        pythonPaths = filter(self.pythonFilter.search, allFiles)
        nDuplicates = 0
        progressCounter = 0

        for pythonFilePath in pythonPaths:
            try:
                fileHash = util.get_cuteHash(pythonFilePath)
                if fileHash not in shaSet:
                    progressCounter = 0
                    shaSet.append(fileHash)
                    cleanFilePath = pythonFilePath[len(path):]
                    if cleanFilePath not in filePathSet:
                        filePathSet.append(cleanFilePath)
                    progressCounter = 1
                    memBeforeAST = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    node = astroid.manager.AstroidManager().ast_from_file(pythonFilePath)
                    memAfterAST = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    warningBoundary = 134217728
                    if memAfterAST - warningBoundary > memBeforeAST:
                        self.logger.warning("Memory usage jumped by " + str(memAfterAST - warningBoundary) + " after creating ast for file: " + pythonFilePath + "memory now at:" + str(memAfterAST))
                    progressCounter = 2
                    endFileScanS = util.getDateTimeS(datetime.datetime.utcnow()) + 10
                    self.extractRegexR(node, report_db, uniqueSourceID, sourceJSON, fileHash, cleanFilePath, citationSet, endFileScanS)
                    progressCounter = 3
                else:
                    nDuplicates += 1
            except RuntimeWarning:
                nowS = util.getDateTimeS(datetime.datetime.utcnow())
                self.logger.warning("PyReS - timeout extracting from file: " + str(pythonFilePath) + " endFileScanS: " + str(endFileScanS) + " nowS: " + str(nowS))
            except Exception as e:
                self.logger.warning("PyReS - problem extracting from file: " + str(pythonFilePath) + " exception: " + str(e) + " progressCounter: " + str(progressCounter))
        self.logger.info("PyReS - finished scanning project. " + " nDuplicates: " + str(nDuplicates) + " uniqueSourceID: " + str(uniqueSourceID) + " sourceJSON: " + sourceJSON)

    # regex_flags = ["IGNORECASE","DEBUG","LOCALE","MULTILINE","DOTALL","UNICODE","VERBOSE"]
    def extractRegexR(self, child, report_db, uniqueSourceID, sourceJSON, fileHash, pythonFilePath, citationSet, endFileScanS):
        from astroid import node_classes, scoped_nodes, bases
        nowS = util.getDateTimeS(datetime.datetime.utcnow())
        if nowS >= endFileScanS:
            raise RuntimeWarning
        # all the methods we will look for
        target_func = self.get_function_list()
        if isinstance(child, node_classes.CallFunc) and child.func.as_string() in target_func:
            # self.log("found targedFunction: " + str(child.func.as_string()))
            regex_citation = [child.func.as_string(), "arg1", "arg2", "arg3", "arg4", "arg5"]
            arg_counter = 1
            for x in child.args:
                if isinstance(x, node_classes.Getattr):
                    key = x.attrname
                    gc = x.get_children().next().infer().next()

                    # for all the cases I've inspected,
                    # we see: x->Name->(Module() or _Yes or Class)
                    if isinstance(gc, scoped_nodes.Module):
                        regex_citation[arg_counter] = gc.globals[key][0].infer().next().as_string()
                    elif isinstance(gc, scoped_nodes.Class):
                        regex_citation[arg_counter] = gc.locals[key][0].infer().next().as_string()
                    elif isinstance(gc, bases._Yes):
                        neighborFile = x.get_children().next()
                        regex_citation[arg_counter] = str(neighborFile.name) + "." + str(x.attrname)
                    else:
                        self.logger.warning("PyReS - extractRegexR, unexpected class found: " + x.__class__.__name__)

                elif isinstance(x, node_classes.Name):
                    regex_citation[arg_counter] = x.infer().next().as_string()
                elif isinstance(x, node_classes.Const):
                    regex_citation[arg_counter] = x.as_string()
                else:
                    self.logger.warning("PyReS - extractRegexR, unexpected class found: " + x.__class__.__name__)
                arg_counter += 1
            regexFunction = target_func.index(regex_citation[0])
            pattern = regex_citation[1]
            flags = self.extract_flags(regexFunction, regex_citation)
            citationTuple = (pythonFilePath, pattern, flags, regexFunction)
            if citationTuple not in citationSet:
                citationSet.append(citationTuple)
                self.record_regex_citation(uniqueSourceID, sourceJSON, fileHash, pythonFilePath, pattern, flags, regexFunction, report_db)
        for grandchild in child.get_children():
            self.extractRegexR(grandchild, report_db, uniqueSourceID, sourceJSON, fileHash, pythonFilePath, citationSet, endFileScanS)

    # ###################### database functions #######################

    def initialize_report(self, report_db):
        self.logger.info("PyReS - initialize_report, report_db: " + report_db)
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute('''CREATE TABLE RegexCitation (uniqueSourceID int, sourceJSON text, fileHash char(44), filePath text, pattern text, flags int, regexFunction int)''')
        c.execute('''CREATE TABLE FilesPerProject (nFiles int, frequency int)''')
        conn.commit()
        conn.close()

    def incrementFilesPerProject(self, numberOfFiles, report_db):
        self.logger.debug("PyReS - incrementing filesPerProject: " + str(numberOfFiles))
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute('''SELECT frequency FROM FilesPerProject WHERE nFiles = ?''', (numberOfFiles,))

        # oldFrequency will be a tuple like (1,) or null
        oldFrequency = c.fetchone()
        if not oldFrequency:
            c.execute("INSERT INTO FilesPerProject values (?,?)", (numberOfFiles, 1))
        else:
            c.execute("UPDATE FilesPerProject SET frequency=? WHERE nFiles=?", (oldFrequency[0] + 1, numberOfFiles))
        conn.commit()
        conn.close()

    def setFilesPerProject(self, numberOfFiles, frequency, report_db):
        self.logger.debug("PyReS - setFilesPerProject, numberOfFiles: " + str(numberOfFiles) + " frequency: " + str(frequency))
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute('''SELECT frequency FROM FilesPerProject WHERE nFiles = ?''', (numberOfFiles,))

        # oldFrequency will be a tuple like (1,) or null
        oldFrequency = c.fetchone()
        if not oldFrequency:
            c.execute("INSERT INTO FilesPerProject values (?,?)", (numberOfFiles, frequency))
        else:
            c.execute("UPDATE FilesPerProject SET frequency=? WHERE nFiles=?", (frequency, numberOfFiles))
        conn.commit()
        conn.close()

    # (uniqueSourceID int, sourceJSON text, scanID int, fileHash char(44), filePath text, pattern text, flags int, regexFunction int)
    def record_regex_citation(self, uniqueSourceID, sourceJSON, fileHash, pythonFilePath, pattern, flags, regexFunction, report_db):
        self.logger.debug("PyReS - record_regex, uniqueSourceID: " + str(uniqueSourceID) + " sourceJSON: " + str(sourceJSON) + " fileHash: " + str(fileHash) + " filePath: " + str(pythonFilePath) + " pattern: " + str(pattern) + " flags: " + str(flags) + " regexFunction: " + str(regexFunction))
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute("INSERT INTO RegexCitation values (?,?,?,?,?,?,?)", (uniqueSourceID, sourceJSON, fileHash, pythonFilePath, pattern, flags, regexFunction))
        conn.commit()
        conn.close()

# ######################### helpers ###################################

    # this is public so that the index mapping is not mysterious
    def get_function_list(self):
        return ["re.compile", "re.search", "re.match", "re.split", "re.findall", "re.finditer", "re.sub", "re.subn"]

    # this is the order in python docs and in our function list
    # re.compile(pattern,flags=0),
    # re.search(pattern,string,flags=0),
    # re.match(pattern,string,flags=0),
    # re.split(pattern,string,maxsplit=0,flags=0),
    # re.findall(pattern,string,flags=0),
    # re.finditer(pattern,string,flags=0),
    # re.sub(pattern,repl,string,count=0,flags=0),
    # re.subn(pattern,repl,string,count=0,flags=0),
    def extract_flags(self, regexFunction, regex_citation):
        if regexFunction == 0:
            # note that arg0 is the regexFunction, arg1 is the pattern
            return regex_citation[2]
        elif regexFunction in [1, 2, 4, 5]:
            return regex_citation[3]
        elif regexFunction == 3:
            return regex_citation[4]
        elif regexFunction in [6, 7]:
            return regex_citation[5]
        else:
            self.logger.error("PyReS - extract_flags, cannot extract flags from " + str(regex_citation))
            return 0


# ######################### basicScanner ##############################


class BasicScanner():
    pythonFilter = re.compile('.*\.py$', re.IGNORECASE)

    def scanDirectory(self, path, report_db, repoID, shaSet, filePathSet, citationSet):
        print "basic scanner scanning path: " + path

        # get a list of the python file paths
        allFiles = []
        for root, dirs, files in os.walk(path):
            for name in files:
                allFiles.append(os.path.join(root, name))
        pythonPaths = filter(self.pythonFilter.search, allFiles)
        nDuplicates = 0
        progressCounter = 0

        for pythonFilePath in pythonPaths:
            try:
                fileHash = util.get_cuteHash(pythonFilePath)
                if fileHash not in shaSet:
                    progressCounter = 0
                    shaSet.append(fileHash)
                    cleanFilePath = pythonFilePath[len(path):]
                    if cleanFilePath not in filePathSet:
                        filePathSet.append(cleanFilePath)
                    progressCounter = 1
                    node = astroid.manager.AstroidManager().ast_from_file(pythonFilePath)
                    progressCounter = 2
                    endFileScanS = util.getDateTimeS(datetime.datetime.utcnow()) + 10
                    self.extractRegexR(node, report_db, repoID, fileHash, cleanFilePath, citationSet, endFileScanS)
                    progressCounter = 3
                else:
                    nDuplicates += 1
            except RuntimeWarning:
                nowS = util.getDateTimeS(datetime.datetime.utcnow())
                print"PyReS - timeout extracting from file: " + str(pythonFilePath) + " endFileScanS: " + str(endFileScanS) + " nowS: " + str(nowS)
            except Exception as e:
                print "PyReS - problem extracting from file: " + str(pythonFilePath) + " exception: " + str(e) + " progressCounter: " + str(progressCounter)
        print "PyReS - finished scanning project. " + " nDuplicates: " + str(nDuplicates)

    # regex_flags = ["IGNORECASE","DEBUG","LOCALE","MULTILINE","DOTALL","UNICODE","VERBOSE"]
    def extractRegexR(self, child, report_db, repoID, fileHash, pythonFilePath, citationSet, endFileScanS):
        from astroid import node_classes, scoped_nodes, bases
        nowS = util.getDateTimeS(datetime.datetime.utcnow())
        if nowS >= endFileScanS:
            raise RuntimeWarning
        # all the methods we will look for
        target_func = self.get_function_list()
        if isinstance(child, node_classes.CallFunc) and child.func.as_string() in target_func:
            # self.log("found targedFunction: " + str(child.func.as_string()))
            regex_citation = [child.func.as_string(), "arg1", "arg2", "arg3", "arg4", "arg5"]
            arg_counter = 1
            for x in child.args:
                if isinstance(x, node_classes.Getattr):
                    key = x.attrname
                    gc = x.get_children().next().infer().next()

                    # for all the cases I've inspected,
                    # we see: x->Name->(Module() or _Yes or Class)
                    if isinstance(gc, scoped_nodes.Module):
                        regex_citation[arg_counter] = gc.globals[key][0].infer().next().as_string()
                    elif isinstance(gc, scoped_nodes.Class):
                        regex_citation[arg_counter] = gc.locals[key][0].infer().next().as_string()
                    elif isinstance(gc, bases._Yes):
                        neighborFile = x.get_children().next()
                        regex_citation[arg_counter] = str(neighborFile.name) + "." + str(x.attrname)
                    else:
                        print "PyReS - extractRegexR, unexpected class found: " + x.__class__.__name__

                elif isinstance(x, node_classes.Name):
                    regex_citation[arg_counter] = x.infer().next().as_string()
                elif isinstance(x, node_classes.Const):
                    regex_citation[arg_counter] = x.as_string()
                else:
                    print "PyReS - extractRegexR, unexpected class found: " + x.__class__.__name__
                arg_counter += 1
            regexFunction = target_func.index(regex_citation[0])
            pattern = regex_citation[1]
            flags = self.extract_flags(regexFunction, regex_citation)
            citationTuple = (pythonFilePath, pattern, flags, regexFunction)
            if citationTuple not in citationSet:
                citationSet.append(citationTuple)
                self.record_regex_citation_basic(repoID, fileHash, pythonFilePath, pattern, flags, regexFunction, report_db)
        for grandchild in child.get_children():
            self.extractRegexR(grandchild, report_db, repoID, fileHash, pythonFilePath, citationSet, endFileScanS)

    def extract_flags(self, regexFunction, regex_citation):
        if regexFunction == 0:
            # note that arg0 is the regexFunction, arg1 is the pattern
            return regex_citation[2]
        elif regexFunction in [1, 2, 4, 5]:
            return regex_citation[3]
        elif regexFunction == 3:
            return regex_citation[4]
        elif regexFunction in [6, 7]:
            return regex_citation[5]
        else:
            return 0

    # this is public so that the index mapping is not mysterious
    def get_function_list(self):
        return ["re.compile", "re.search", "re.match", "re.split", "re.findall", "re.finditer", "re.sub", "re.subn"]

    # (uniqueSourceID int, sourceJSON text, scanID int, fileHash char(44), filePath text, pattern text, flags int, regexFunction int)
    def record_regex_citation_basic(self, repoID, fileHash, pythonFilePath, pattern, flags, regexFunction, report_db):
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute("INSERT INTO RegexCitation values (?,?,?,?,?,?)", (repoID, fileHash, pythonFilePath, pattern, flags, regexFunction))
        conn.commit()
        conn.close()

    def incrementFilesPerProject(self, numberOfFiles, report_db):
        conn = sqlite3.connect(report_db)
        c = conn.cursor()
        c.execute('''SELECT frequency FROM FilesPerProject WHERE nFiles = ?''', (numberOfFiles,))

        # oldFrequency will be a tuple like (1,) or null
        oldFrequency = c.fetchone()
        if not oldFrequency:
            c.execute("INSERT INTO FilesPerProject values (?,?)", (numberOfFiles, 1))
        else:
            c.execute("UPDATE FilesPerProject SET frequency=? WHERE nFiles=?", (oldFrequency[0] + 1, numberOfFiles))
        conn.commit()
        conn.close()
