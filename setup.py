from setuptools import setup

setup(
    name='wowahf',
    version='0.2.3',
    packages=['wowahf', 'wowahf.cfg', 'wowahf.db', 'wowahf.db.models', 'wowahf.parser', 'wowahf.utils'],
    url='https://github.com/Locchan/WoW-AHF',
    license='',
    author='Locchan',
    author_email='locchan@protonmail.com',
    description='Auction data gatherer for WoW',
    scripts=['wowahf/wowahf'],
    install_requires=[
        'python-blizzardapi',
        'mysql-connector-python',
        'sqlalchemy',
        'hvac'
    ]
)
