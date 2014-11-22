import sqlite3
import os


class Depot:

    # if the tour.db file is not yet defined, or we are in recreate mode, create it
    def __init__(self):
        dbpath = self.getPath() + self.getDB()
        if self.shouldRenew():
            try:
                os.remove(dbpath)
            except OSError:
                pass
        if not os.path.isfile(dbpath):
            conn = sqlite3.connect(Depot.DB)
            c = conn.cursor()
            c.execute('''CREATE TABLE name (Tour) (id text, start int, last int, nFiles int, status text, scanner text, gmail text, download text)''')
            conn.commit()
            conn.close()

    # we don't want any accidental modification of this, so it's get only
    def getPath(self):
        return '/Users/carlchapman/Documents/SoftwareProjects/temp/'

    def getDB(self):
        return 'tour.db'

    def shouldRenew():
        return False


def test_depot():
    print "testing"
