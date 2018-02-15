import os
import re
import sys
import time

import distance
import json
from collections import deque

import selenium
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.common.by import By

import requests
import tqdm


class SemanticScholarScrapper(object):
    """
    A Web Scrapper for Semantic Scholar.
    """

    def __init__(self, timeout=5, time_between_api_call=0.5, headless=True,
                 site_url='https://www.semanticscholar.org/'):
        """

        :param timeout: Number of seconds the web driver should wait before raising a timeout error.
        :param time_between_api_call: Time in seconds between api calls.
        :param headless: If set to be true, the web driver launches a browser (chrome) in silent mode.
        :param site_url: Home for semantic scholar search engine.
        """

        self._site_url = site_url
        self._web_driver: webdriver.Chrome = None
        self._timeout = timeout
        self._time_between_api_call = time_between_api_call
        self._headless = headless

    def get_related_papers(self, papers_dict: dict, out_dir_name: str=None) -> None:
        """
        Given a dictionary of papers --- each paper is a dictionary retrieved resorting to semantic api --- this method
        retrieves the related papers dictionaries in a breadth first search fashion.

        :param papers_dict: Dictionary of papers, where keys stand for paper ids and values for dictionaries retrieved by
        semantic scholar api.
        :param out_dir_name: Directory to which retrieved json files should be saved.

        :return:
        """

        paper_id_queue = deque(papers_dict.keys())
        paper_visited_set = set()

        while len(paper_id_queue) > 0:
            current_paper_id = paper_id_queue.popleft()

            if current_paper_id not in paper_visited_set:
                paper_visited_set.add(current_paper_id)
                self._retrieve_related_papers(papers_dict, current_paper_id, 'references', out_dir_name)
                self._retrieve_related_papers(papers_dict, current_paper_id, 'citations', out_dir_name)

    def _retrieve_related_papers(self, papers_dict: dict, paper_id: str, relationship_type: str,
                                 paper_visited_set: set, paper_id_queue: deque,
                                 out_dir_name=None):
        """
        Given a paper id, this method retrieves its citations and references (dictionaries) from semantic scholar.

        :param papers_dict: Dictionary of papers, where keys stand for paper ids and values for dictionaries retrieved by
        semantic scholar api.
        :param paper_id: Semantic scholar paper id.
        :param relationship_type: 'references' or 'citations'.
        :param paper_visited_set: Set used in breadth first search.
        :param paper_id_queue: Queue used in breadth first search.
        :param out_dir_name: Directory to which retrieved json files should be saved.

        :return:
        """

        related_papers_id_list = [paper_dict['paperId']
                                  for paper_dict in papers_dict[paper_id][relationship_type]
                                  if paper_dict['paperId'] != '']

        for related_paper_id in tqdm.tqdm(related_papers_id_list):
            if related_paper_id not in papers_dict:
                related_paper_dict = self._get_paper_json_by_id(related_paper_id)
                papers_dict[related_paper_id] = related_paper_dict

                if out_dir_name is not None:
                    self.save_json(related_paper_dict, out_dir_name)

                if related_paper_id not in paper_visited_set:
                    paper_id_queue.append(related_paper_id)

    def save_json(self, paper_dict: dict, out_dir_name) -> None:
        """
        Given a paper dictionary, this method serializes and writes it to disk.

        :param paper_dict: A paper dictionary.
        :param out_dir_name: Directory to which this paper json should be saved.

        :return:
        """

        output_file_name = self.create_json_file_name(paper_dict, out_dir_name)
        with open(output_file_name, 'w') as out_file:
            json.dump(paper_dict, out_file, indent=4, sort_keys=True)

    @staticmethod
    def create_json_file_name(paper_dict: dict, out_dir_name: str) -> str:
        """
        Given a paper dictionary, this method returns its serialized name.

        :param paper_dict: A paper dictionary.
        :param out_dir_name: Directory to which this paper json should be saved.

        :return: Serialized name.
        """

        max_no_chars = 50
        title = paper_dict['title']
        title = '_'.join(re.findall('\w+', title)).lower()[:max_no_chars]
        output_file_name = '{}.json'.format(os.path.join(out_dir_name, title))

        return output_file_name

    def scrap_paper_list_by_title(self, paper_title_list: list) -> dict:
        """
        Given a list of paper titles, this method retrieves their associated data from semantic scholar.

        :param paper_title_list: A list of paper titles.

        :return: A dictionary of dictionaries containing papers data.
        """

        self._start_browser()
        papers_dict = dict()

        for paper_name in tqdm.tqdm(paper_title_list):
            try:
                paper_dict = self.scrap_paper_by_title(paper_name, call_browser=False)
                paper_id = paper_dict['paperId']
                papers_dict[paper_id] = paper_dict
            except KeyError:
                pass

        self._close_browser()

        return papers_dict

    def scrap_paper_by_title(self, paper_title: str, call_browser=True) -> dict:
        """
        Given a paper title, this method retrieves its associated data from semantic scholar.

        :param paper_title: A paper title.
        :param call_browser: True when web browser hasn't be started yet.

        :return: Data dictionary for paper.
        """

        attributes_dict = dict()

        if call_browser:
            self._start_browser()

        try:
            self._search_paper_by_name(paper_title)
            self._open_first_link_in_search_page()
            self._check_paper_page(paper_title)

            abstract = self._get_abstract_in_paper_page()
            topic_dict = self._get_topics_in_paper_page()
            bibtex_citation = self._get_bibtex_citation()
            attributes_dict = self._get_paper_json_by_id(self._get_paper_id_from_current_url())

            attributes_dict['abstract'] = abstract
            attributes_dict['topics'] = topic_dict
            attributes_dict['bibtex_citation'] = bibtex_citation

            if call_browser:
                self._close_browser()

        except FirstPaperDifferentError:
            self._close_browser()

        finally:
            return attributes_dict

    def _check_paper_page(self, paper_title):
        """
        Check if opened paper web page is indeed related to paper title.

        :param paper_title: A paper title.

        :return:
        """

        self._wait_element_by_tag_name('h1')

        h1_list = self._web_driver.find_elements_by_tag_name('h1')
        h1 = [h1 for h1 in h1_list if h1.get_attribute('data-selenium-selector') == 'paper-detail-title'][0]
        title = h1.text

        if not 0 <= distance.levenshtein(paper_title, title) <= 10:
            raise FirstPaperDifferentError

    def _get_paper_json_by_id(self, paper_id):
        """
        Retrieves paper data by paper id.

        :param paper_id: A paper id

        :return: Paper data dictionary.
        """

        api_url = 'https://api.semanticscholar.org/v1/paper/'
        json_url = '{}{}?include_unknown_references=true'.format(api_url, paper_id)

        try:
            request = requests.get(json_url)
        except requests.exceptions.RequestException:
            raise

        time.sleep(self._time_between_api_call)
        return request.json()

    def _get_paper_id_from_current_url(self) -> str:
        paper_page_id = self._get_current_page_url().split('/')[-1]
        return re.findall('\w+(?=\?*)', paper_page_id)[0]

    def _get_current_page_url(self) -> str:
        return self._web_driver.current_url

    def _get_bibtex_citation(self) -> str:
        """
        Parse page for bibtex citation.

        :return: Bibtex citation string.
        """

        self._wait_element_by_class_name('formatted-citation--style-bibtex')

        bibtex_box = self._web_driver.find_element_by_class_name('formatted-citation--style-bibtex')
        bibtex_citation = bibtex_box.text

        return bibtex_citation

    def _get_topics_in_paper_page(self) -> dict:
        """
        Parse page for topic dict ({topic: url}).

        :return: Topic dict.
        """

        try:
            self._wait_element_by_class_name('entities')

            topics_div = self._web_driver.find_element_by_class_name('entities')
            topics_ul = topics_div.find_element_by_tag_name('ul')
            topic_dict = {a.text: a.get_attribute('href') for a in topics_ul.find_elements_by_tag_name('a')}

            return topic_dict

        except TimeoutException:
            return {}

    def _get_abstract_in_paper_page(self) -> str:
        """
        Parse page for abstract text.

        :return: Abstract text.
        """

        self._wait_element_by_class_name('mod-clickable')

        try:
            more_button = self._web_driver.find_element_by_class_name('mod-clickable')
            more_button.click()
        except selenium.common.exceptions.ElementNotVisibleException:
            pass

        abstract_div = self._web_driver.find_element_by_class_name('text-truncator')
        abstract_text = abstract_div.text

        return abstract_text

    def _open_first_link_in_search_page(self) -> None:
        """
        Given the browser is on a search page, go to the first paper link.
        """

        self._wait_element_by_class_name('search-result-title')

        papers_div = self._web_driver.find_element_by_class_name('search-result-title')
        first_paper_link = papers_div.find_element_by_tag_name('a')

        first_paper_link.click()

    def _search_paper_by_name(self, paper_title: str) -> None:
        """
        Go to the search page for 'paper_name'.
        """

        self._web_driver.get(self._site_url)
        self._wait_element_by_name('q')

        input_search_box = self._web_driver.find_element_by_name('q')
        input_search_box.send_keys(paper_title)
        input_search_box.send_keys(Keys.ENTER)

    def _wait_element_by_tag_name(self, tag_name) -> None:
        """
        Make driver wait while web browser loads tags with specific name.
        """

        try:
            element_present = expected_conditions.presence_of_element_located((By.TAG_NAME, tag_name))
            WebDriverWait(self._web_driver, self._timeout).until(element_present)
        except TimeoutException:
            raise

    def _wait_element_by_name(self, name):
        """
        Make driver wait while browser loads elements with specific name.
        """

        try:
            element_present = expected_conditions.presence_of_element_located((By.NAME, name))
            WebDriverWait(self._web_driver, self._timeout).until(element_present)
        except TimeoutException:
            raise

    def _wait_element_by_class_name(self, class_name):
        """
        Make driver wait while browser loads elements with specific class name.
        """

        try:
            element_present = expected_conditions.presence_of_element_located((By.CLASS_NAME, class_name))
            WebDriverWait(self._web_driver, self._timeout).until(element_present)
        except TimeoutException:
            raise

    def _start_browser(self):
        chrome_options = Options()
        if self._headless:
            chrome_options.add_argument("--headless")

        self._web_driver = webdriver.Chrome(chrome_options=chrome_options)

    def _close_browser(self):
        self._web_driver.close()


class FirstPaperDifferentError(Exception):

    def __init__(self, expression, message):
        self.expression = expression
        self.message = message


def main():
    ss_scrapper = SemanticScholarScrapper(headless=True)
    jsons_output_path = '/home/daniel/PycharmProjects/SemanticScholar/output2'
    papers_dir = '/home/daniel/Documents/LNCC/Doutorado/dissertation-paper-project/reference-papers/Evaluation'
    paper_list = [os.path.splitext(paper_fn)[0].strip() for paper_fn in os.listdir(papers_dir)]
    papers_dict = ss_scrapper.scrap_paper_list_by_title(paper_list)

    try:
        os.mkdir(jsons_output_path)
    except FileExistsError:
        pass

    for paper_id in papers_dict:
        try:
            output_file_name = ss_scrapper.create_json_file_name(papers_dict[paper_id], jsons_output_path)
            with open(output_file_name, 'w') as out_file:
                json.dump(papers_dict[paper_id], out_file, sort_keys=True, indent=4)
        except KeyError:
            # If paper_dict is a empty dictionary
            pass


if __name__ == '__main__':
    sys.exit(main())
