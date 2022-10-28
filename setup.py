from setuptools import setup

setup(
    name='wow-ahf',
    version='0.0.1a',
    packages=['wowahf'],
    url='',
    license='',
    author='Locchan',
    author_email='locchan@protonmail.com',
    description='Auction data gatherer for WoW',
    scripts=['wowahf/wowahf.py'],
    install_requires=[
        'python-blizzardapi',
        'mysql-connector-python',
        'sqlalchemy'
    ]
)
