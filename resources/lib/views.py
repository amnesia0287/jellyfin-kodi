# -*- coding: utf-8 -*-

#################################################################################################

import logging
import os
import shutil
import urllib
import xml.etree.ElementTree as etree

import xbmc
import xbmcvfs

import downloader as server
from database import Database, emby_db, get_sync, save_sync
from objects.kodi import kodi
from helper import _, api, indent, write_xml, window
from emby import Emby

#################################################################################################

LOG = logging.getLogger("EMBY."+__name__)
NODES = {
    'tvshows': [
        ('all', None),
        ('recent', _(30170)),
        ('recentepisodes', _(30175)),
        ('inprogress', _(30171)),
        ('inprogressepisodes', _(30178)),
        ('nextepisodes', _(30179)),
        ('genres', 135),
        ('random', _(30229)),
        ('recommended', _(30230))
    ],
    'movies': [
        ('all', None),
        ('recent', _(30174)),
        ('inprogress', _(30177)),
        ('unwatched', _(30189)),
        ('sets', 20434),
        ('genres', 135),
        ('random', _(30229)),
        ('recommended', _(30230))
    ],
    'musicvideos': [
        ('all', None),
        ('recent', _(30256)),
        ('inprogress', _(30257)),
        ('unwatched', _(30258))
    ],
    'homevideos': [
        ('all', None),
        ('recent', _(30251)),
        ('recommended', _(30253))
    ],
    'photos': [
        ('all', None),
        ('recent', _(30252)),
        ('sets', _(30255)),
        ('recommended', _(30254))
    ]
}

#################################################################################################


def verify_kodi_defaults():

    ''' Make sure we have the kodi default folder in place.
    '''
    node_path = xbmc.translatePath("special://profile/library/video").decode('utf-8')

    if not xbmcvfs.exists(node_path):
        try:
            shutil.copytree(
                src=xbmc.translatePath("special://xbmc/system/library/video").decode('utf-8'),
                dst=xbmc.translatePath("special://profile/library/video").decode('utf-8'))
        except Exception as error:
            xbmcvfs.mkdir(node_path)

    for index, node in enumerate(['movies', 'tvshows', 'musicvideos']):
        file = os.path.join(node_path, node, "index.xml")
        xml = etree.parse(file).getroot()
        xml.set('order', str(17 + index))
        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    playlist_path = xbmc.translatePath("special://profile/playlists/video").decode('utf-8')

    if not xbmcvfs.exists(playlist_path):
        xbmcvfs.mkdirs(playlist_path)

class Views(object):

    sync = None
    limit = 25

    def __init__(self):

        self.sync = get_sync()
        self.window = {}
        self.server = Emby()

    def add_library(self, view):

        ''' Add entry to view table in emby database.
        '''
        with Database('emby') as embydb:
            emby_db.EmbyDatabase(embydb.cursor).add_view(view['Id'], view['Name'], view['Media'])

    def remove_library(self, view_id):

        ''' Remove entry from view table in emby database.
        '''
        with Database('emby') as embydb:
            emby_db.EmbyDatabase(embydb.cursor).remove_view(view_id)

        self.delete_playlist_by_id(view_id)
        self.delete_node_by_id(view_id)
        self.get_views()

    def get_views(self):
        
        ''' Get the media folders. Add or remove them.
        '''
        media = {
            'movies': "Movie",
            'tvshows': "Series",
            'musicvideos': "MusicVideo"
        }
        try:
            libraries = self.server['api'].get_media_folders()['Items']
        except Exception as error:
            LOG.error("Unable to process libraries: %s", error)

            return

        self.sync['SortedViews'] = [x['Id'] for x in libraries]

        for library in libraries:

            if library['Type'] == 'Channel':
                library['Media'] = "channels"
            else:
                library['Media'] = library.get('OriginalCollectionType', library.get('CollectionType', "mixed"))

            self.add_library(library)

        save_sync(self.sync)

    def get_nodes(self):
        
        ''' Set up playlists, video nodes, window prop.
        '''
        node_path = xbmc.translatePath("special://profile/library/video").decode('utf-8')
        playlist_path = xbmc.translatePath("special://profile/playlists/video").decode('utf-8')
        index = 0

        with Database('emby') as embydb:
            db = emby_db.EmbyDatabase(embydb.cursor)

            for library in self.sync['Whitelist']:

                library = library.replace('Mixed:', "")
                view = db.get_view(library)
                view = {'Id': library, 'Name': view[0], 'Tag': view[0], 'Media': view[1]}

                if view['Media'] == 'mixed':
                    for media in ('movies', 'tvshows'):

                        temp_view = dict(view)
                        temp_view['Media'] = media
                        self.add_playlist(playlist_path, temp_view, True)
                        self.add_nodes(node_path, temp_view, True)
                    else: # Compensate for the duplicate.
                        index += 1
                else:
                    if view['Media'] in ('movies', 'tvshows', 'musicvideos'):
                        self.add_playlist(playlist_path, view)

                    if view['Media'] not in ('music'):
                        self.add_nodes(node_path, view)

                index += 1

        for single in [{'Name': _('fav_movies'), 'Tag': "Favorite movies", 'Media': "movies"},
                       {'Name': _('fav_tvshows'), 'Tag': "Favorite tvshows", 'Media': "tvshows"},
                       {'Name': _('fav_episodes'), 'Tag': "Favorite episodes", 'Media': "episodes"}]:
            
            self.add_single_node(node_path, index, "favorites", single)
            index += 1

        self.window_nodes()

    def add_playlist(self, path, view, mixed=False):
        
        ''' Create or update the xps file.
        '''
        file = os.path.join(path, "emby%s%s.xsp" % (view['Media'], view['Id']))
        
        try:
            xml = etree.parse(file).getroot()
        except Exception:
            xml = etree.Element('smartplaylist', {'type': view['Media']})
            etree.SubElement(xml, 'name')
            etree.SubElement(xml, 'match')

        name = xml.find('name')
        name.text = view['Name'] if not mixed else "%s (%s)" % (view['Name'], view['Media'])

        match = xml.find('match')
        match.text = "all"

        for rule in xml.findall('.//value'):
            if rule.text == view['Tag']:
                break
        else:
            rule = etree.SubElement(xml, 'rule', {'field': "tag", 'operator': "is"})
            etree.SubElement(rule, 'value').text = view['Tag']

        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    def add_nodes(self, path, view, mixed=False):

        ''' Create or update the video node file.
        '''
        folder = os.path.join(path, "emby%s%s" % (view['Media'], view['Id']))

        if not xbmcvfs.exists(folder):
            xbmcvfs.mkdir(folder)

        self.node_index(folder, view, mixed)

        if view['Media'] == 'tvshows':
            self.node_tvshow(folder, view)
        else:
            self.node(folder, view)

    def add_single_node(self, path, index, item_type, view):

        file = os.path.join(path, "emby_%s.xml" % view['Tag'].replace(" ", ""))

        try:
            xml = etree.parse(file).getroot()
        except Exception:
            xml = self.node_root('folder' if item_type == 'favorites' and view['Media'] == 'episodes' else 'filter', index)
            etree.SubElement(xml, 'label')
            etree.SubElement(xml, 'match')
            etree.SubElement(xml, 'content')

        label = xml.find('label')
        label.text = view['Name']

        content = xml.find('content')
        content.text = view['Media']

        match = xml.find('match')
        match.text = "all"

        if view['Media'] != 'episodes':

            for rule in xml.findall('.//value'):
                if rule.text == view['Tag']:
                    break
            else:
                rule = etree.SubElement(xml, 'rule', {'field': "tag", 'operator': "is"})
                etree.SubElement(rule, 'value').text = view['Tag']

        if item_type == 'favorites' and view['Media'] == 'episodes':
            path = self.window_browse(view, 'FavEpisodes')
            self.node_favepisodes(xml, path)
        else:
            self.node_all(xml)

        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    def node_root(self, root, index):

        ''' Create the root element
        '''
        if root == 'main':
            element = etree.Element('node', {'order': str(index)})
        elif root == 'filter':
            element = etree.Element('node', {'order': str(index), 'type': "filter"})
        else:
            element = etree.Element('node', {'order': str(index), 'type': "folder"})

        etree.SubElement(element, 'icon').text = "special://home/addons/plugin.video.emby/icon.png"

        return element

    def node_index(self, folder, view, mixed=False):

        file = os.path.join(folder, "index.xml")
        index = self.sync['SortedViews'].index(view['Id'])

        try:
            xml = etree.parse(file).getroot()
            xml.set('order', str(index))
        except Exception:
            xml = self.node_root('main', index)
            etree.SubElement(xml, 'label')

        label = xml.find('label')
        label.text = view['Name'] if not mixed else "%s (%s)" % (view['Name'], _(view['Media']))

        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    def node(self, folder, view):

        for node in NODES[view['Media']]:

            xml_name = node[0]
            xml_label = node[1] or view['Name'].encode('utf-8')
            file = os.path.join(folder, "%s.xml" % xml_name)
            self.add_node(NODES[view['Media']].index(node), file, view, xml_name, xml_label)

    def node_tvshow(self, folder, view):

        for node in NODES[view['Media']]:

            xml_name = node[0]
            xml_label = node[1] or view['Name'].encode('utf-8')
            xml_index = NODES[view['Media']].index(node)
            file = os.path.join(folder, "%s.xml" % xml_name)

            if xml_name == 'nextepisodes':
                path = self.window_nextepisodes(view)
                self.add_dynamic_node(xml_index, file, view, xml_name, xml_label, path)
            else:
                self.add_node(xml_index, file, view, xml_name, xml_label)

    def add_node(self, index, file, view, node, name):

        try:
            xml = etree.parse(file).getroot()
        except Exception:
            xml = self.node_root('filter', index)
            etree.SubElement(xml, 'label')
            etree.SubElement(xml, 'match')
            etree.SubElement(xml, 'content')

        label = xml.find('label')
        label.text = str(name) if type(name) == int else name

        content = xml.find('content')
        content.text = view['Media']

        match = xml.find('match')
        match.text = "all"

        for rule in xml.findall('.//value'):
            if rule.text == view['Tag']:
                break
        else:
            rule = etree.SubElement(xml, 'rule', {'field': "tag", 'operator': "is"})
            etree.SubElement(rule, 'value').text = view['Tag']

        getattr(self, 'node_' + node)(xml)
        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    def add_dynamic_node(self, index, file, view, node, name, path):

        try:
            xml = etree.parse(file).getroot()
        except Exception:
            xml = self.node_root('folder', index)
            etree.SubElement(xml, 'label')
            etree.SubElement(xml, 'content')

        label = xml.find('label')
        label.text = name

        getattr(self, 'node_' + node)(xml, path)
        indent(xml)
        write_xml(etree.tostring(xml, 'UTF-8'), file)

    def node_all(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "sorttitle":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "ascending"}).text = "sorttitle"

    def node_nextepisodes(self, root, path):

        for rule in root.findall('.//path'):
            rule.text = path
            break
        else:
            etree.SubElement(root, 'path').text = path

        for rule in root.findall('.//content'):
            rule.text = "episodes"
            break
        else:
            etree.SubElement(root, 'content').text = "episodes"

    def node_recent(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "dateadded":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "descending"}).text = "dateadded"

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)

        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'playcount':
                rule.find('value').text = "0"
                break
        else:
            rule = etree.SubElement(root, 'rule', {'field': "playcount", 'operator': "is"})
            etree.SubElement(rule, 'value').text = "0"

    def node_inprogress(self, root):

        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'inprogress':
                break
        else:
            etree.SubElement(root, 'rule', {'field': "inprogress", 'operator': "true"})

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)

    def node_genres(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "sorttitle":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "ascending"}).text = "sorttitle"

        for rule in root.findall('.//group'):
            rule.text = "genres"
            break
        else:
            etree.SubElement(root, 'group').text = "genres"
        
    def node_unwatched(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "sorttitle":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "ascending"}).text = "sorttitle"

        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'playcount':
                rule.find('value').text = "0"
                break
        else:
            rule = etree.SubElement(root, "rule", {'field': "playcount", 'operator': "is"})
            etree.SubElement(rule, 'value').text = "0"

    def node_sets(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "sorttitle":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "ascending"}).text = "sorttitle"

        for rule in root.findall('.//group'):
            rule.text = "sets"
            break
        else:
            etree.SubElement(root, 'group').text = "sets"

    def node_random(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "random":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "ascending"}).text = "random"

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)

    def node_recommended(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "rating":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "descending"}).text = "rating"

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)
        
        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'playcount':
                rule.find('value').text = "0"
                break
        else:
            rule = etree.SubElement(root, 'rule', {'field': "playcount", 'operator': "is"})
            etree.SubElement(rule, 'value').text = "0"

        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'rating':
                rule.find('value').text = "7"
                break
        else:
            rule = etree.SubElement(root, 'rule', {'field': "rating", 'operator': "greaterthan"})
            etree.SubElement(rule, 'value').text = "7"

    def node_recentepisodes(self, root):

        for rule in root.findall('.//order'):
            if rule.text == "dateadded":
                break
        else:
            etree.SubElement(root, 'order', {'direction': "descending"}).text = "dateadded"

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)

        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'playcount':
                rule.find('value').text = "0"
                break
        else:
            rule = etree.SubElement(root, 'rule', {'field': "playcount", 'operator': "is"})
            etree.SubElement(rule, 'value').text = "0"

        content = root.find('content')
        content.text = "episodes"

    def node_inprogressepisodes(self, root):

        for rule in root.findall('.//limit'):
            rule.text = str(self.limit)
            break
        else:
            etree.SubElement(root, 'limit').text = str(self.limit)
        
        for rule in root.findall('.//rule'):
            if rule.attrib['field'] == 'inprogress':
                break
        else:
            etree.SubElement(root, 'rule', {'field': "inprogress", 'operator':"true"})

        content = root.find('content')
        content.text = "episodes"

    def node_favepisodes(self, root, path):

        for rule in root.findall('.//path'):
            rule.text = path
            break
        else:
            etree.SubElement(root, 'path').text = path

        for rule in root.findall('.//content'):
            rule.text = "episodes"
            break
        else:
            etree.SubElement(root, 'content').text = "episodes"


    def order_media_folders(self, folders):

        ''' Returns a list of sorted media folders based on the Emby views.
            Insert them in SortedViews and remove Views that are not in media folders.
        '''
        if not folders:
            return folders

        sorted_views = list(self.sync['SortedViews'])
        unordered = [x[0] for x in folders]
        grouped = [x for x in unordered if x not in sorted_views]

        for library in grouped:
            sorted_views.append(library)

        sorted_folders = [x for x in sorted_views if x in unordered]

        return [folders[unordered.index(x)] for x in sorted_folders]

    def window_nodes(self):

        ''' Just read from the database and populate based on SortedViews
            Setup the window properties that reflect the emby server views and more.
        '''
        self.window_clear()

        with Database('emby') as embydb:
            libraries = emby_db.EmbyDatabase(embydb.cursor).get_views()

        libraries = self.order_media_folders(libraries or [])
        index = 0

        for library in (libraries or []):
            view = {'Id': library[0], 'Name': library[1], 'Tag': library[1], 'Media': library[2]}

            if library[0] in self.sync['Whitelist']: # Synced libraries

                if view['Media'] in ('movies', 'tvshows', 'musicvideos', 'mixed'):
                    for node in NODES[view['Media']]:

                        if view['Media'] == 'mixed':
                            for media in ('movies', 'tvshows'):

                                temp_view = dict(view)
                                temp_view['Media'] = media
                                temp_view['Name'] = "%s (%s)" % (view['Name'], _(media))
                                self.window_node(index, temp_view, *node)
                            else: # Add one to compensate for the duplicate.
                                index += 1
                        else:
                            self.window_node(index, view, *node)

                elif view['Media'] == 'music':
                    self.window_node(index, view, 'music')
            else: # Dynamic entry
                self.window_node(index, view, 'browse')

            index += 1

        for single in [{'Name': _('fav_movies'), 'Tag': "Favorite movies", 'Media': "movies"},
                       {'Name': _('fav_tvshows'), 'Tag': "Favorite tvshows", 'Media': "tvshows"},
                       {'Name': _('fav_episodes'), 'Tag': "Favorite episodes", 'Media': "episodes"}]:
            
            self.window_single_node(index, "favorites", single)
            index += 1

        window('Emby.nodes.total', str(index))

    def window_node(self, index, view, node=None, node_label=None):

        ''' Leads to another listing of nodes.
        '''
        if view['Media'] in ('homevideos', 'photos'):
            path = self.window_browse(view, None if node in ('all', 'browse') else node)
        elif node == 'nextepisodes':
            path = self.window_nextepisodes(view)
        elif node == 'music':
            path = self.window_music(view)
        elif node == 'browse':
            path = self.window_browse(view)
        else:
            path = self.window_path(view, node)

        if node == 'music':
            window_path = "ActivateWindow(Music,%s,return)" % path
        elif node in ('browse', 'homevideos', 'photos'):
            window_path = path
        else:
            window_path = "ActivateWindow(Videos,%s,return)" % path

        if node in ('all', 'music'):

            window_prop = "Emby.nodes.%s" % index
            window('%s.index' % window_prop, path.replace('all.xml', "")) # dir
            window('%s.title' % window_prop, view['Name'])
            window('%s.content' % window_prop, path)

        elif node == 'browse':

            window_prop = "Emby.nodes.%s" % index
            window('%s.title' % window_prop, view['Name'])
        else:
            window_prop = "Emby.nodes.%s.%s" % (index, node)
            window('%s.title' % window_prop, str(node_label) or view['Name'])
            window('%s.content' % window_prop, path)

        window('%s.id' % window_prop, view['Id'])
        window('%s.path' % window_prop, window_path)
        window('%s.type' % window_prop, view['Media'])

        if self.server['connected']:

            artwork = api.API(None, self.server['auth/server-address']).get_artwork(view['Id'], 'Primary')
            window('%s.artwork' % window_prop, artwork)

    def window_single_node(self, index, item_type, view):

        ''' Single destination node.
        '''
        path = "library://video/emby_%s.xml" % view['Tag'].replace(" ", "")
        window_path = "ActivateWindow(Videos,%s,return)" % path

        window_prop = "Emby.nodes.%s" % index
        window('%s.title' % window_prop, view['Name'])
        window('%s.path' % window_prop, window_path)
        window('%s.content' % window_prop, path)
        window('%s.type' % window_prop, item_type)

    def window_path(self, view, node):
        return "library://video/emby%s%s/%s.xml" % (view['Media'], view['Id'], node)

    def window_music(self, view):
        return "library://music/"

    def window_nextepisodes(self, view):

        params = {
            'id': view['Id'],
            'mode': "nextepisodes",
            'limit': self.limit
        }
        return "%s?%s" % ("plugin://plugin.video.emby", urllib.urlencode(params))

    def window_browse(self, view, node=None):

        params = {
            'mode': "browse",
            'type': view['Media']
        }

        if view.get('Id'):
            params['id'] = view['Id']

        if node:
            params['folder'] = node

        return "%s?%s" % ("plugin://plugin.video.emby", urllib.urlencode(params))

    def window_clear(self):

        ''' Clearing window prop setup for Views.
        '''
        total = int(window('Emby.nodes.total') or 0)
        props = [
        
            "index","id","path","title","content","type"
            "inprogress.content","inprogress.title",
            "inprogress.content","inprogress.path",
            "nextepisodes.title","nextepisodes.content",
            "nextepisodes.path","unwatched.title",
            "unwatched.content","unwatched.path",
            "recent.title","recent.content","recent.path",
            "recentepisodes.title","recentepisodes.content",
            "recentepisodes.path","inprogressepisodes.title",
            "inprogressepisodes.content","inprogressepisodes.path"
        ]
        for i in range(total):
            for prop in props:
                window('Emby.nodes.%s.%s' % (str(i), prop), clear=True)

    def delete_playlist(self, path):

        xbmcvfs.delete(path)
        LOG.info("DELETE playlist %s", path)

    def delete_playlists(self):
        
        ''' Remove all emby playlists.
        '''
        path = xbmc.translatePath("special://profile/playlists/video/").decode('utf-8')
        _, files = xbmcvfs.listdir(path)
        for file in files:
            if file.decode('utf-8').startswith('emby'):
                self.delete_playlist(os.path.join(path, file.decode('utf-8')))

    def delete_playlist_by_id(self, view_id):

        ''' Remove playlist based based on view_id.
        '''
        path = xbmc.translatePath("special://profile/playlists/video/").decode('utf-8')
        _, files = xbmcvfs.listdir(path)
        for file in files:
            file = file.decode('utf-8')

            if file.startswith('emby') and file.endswith('%s.xsp' % view_id):
                self.delete_playlist(os.path.join(path, file.decode('utf-8')))

    def delete_node(self, path):

        xbmcvfs.delete(path)
        LOG.info("DELETE node %s", path)

    def delete_nodes(self):

        ''' Remove node and children files.
        '''
        path = xbmc.translatePath("special://profile/library/video/").decode('utf-8')
        dirs, files = xbmcvfs.listdir(path)

        for file in files:

            if file.startswith('emby'):
                self.delete_node(os.path.join(path, file.decode('utf-8')))

        for directory in dirs:

            if directory.startswith('emby'):
                _, files = xbmcvfs.listdir(os.path.join(path, directory.decode('utf-8')))

                for file in files:
                    self.delete_node(os.path.join(path, directory.decode('utf-8'), file.decode('utf-8')))

                xbmcvfs.rmdir(os.path.join(path, directory.decode('utf-8')))

    def delete_node_by_id(self, view_id):

        ''' Remove node and children files based on view_id.
        '''
        path = xbmc.translatePath("special://profile/library/video/").decode('utf-8')
        dirs, files = xbmcvfs.listdir(path)

        for directory in dirs:

            if directory.startswith('emby') and directory.endswith(view_id):
                _, files = xbmcvfs.listdir(os.path.join(path, directory.decode('utf-8')))

                for file in files:
                    self.delete_node(os.path.join(path, directory.decode('utf-8'), file.decode('utf-8')))

                xbmcvfs.rmdir(os.path.join(path, directory.decode('utf-8')))
