"""
MicroAgents DSL Parser
Parseur personnalisé pour le Domain Specific Language des MicroAgents
"""

from __future__ import annotations

import re
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import warnings

from microagents.dsl.language.lexer import DSLToken, DSLLexer, LexerError
from microagents.dsl.language.ast import (
    ASTNode, AgentDefinition, Capability, InputDefinition,
    OutputDefinition, RuleDefinition, BusinessValueDefinition,
    Dependency, Configuration, TestSpec, ComplianceRequirement,
    DocumentationBlock, ImportStatement, VersionConstraint,
    DSLSyntaxError, Position
)


class ParseError(Exception):
    """Erreur de parsing DSL"""
    def __init__(self, message: str, position: Position, suggestion: str = ""):
        self.message = message
        self.position = position
        self.suggestion = suggestion
        super().__init__(f"{message} at {position.line}:{position.column}. {suggestion}")


class ParserState:
    """État du parser pendant l'analyse"""
    def __init__(self):
        self.current_line = 1
        self.current_column = 1
        self.indent_level = 0
        self.in_block = False
        self.current_agent: Optional[str] = None
        self.imports: List[ImportStatement] = []
        self.version_constraints: Dict[str, VersionConstraint] = {}
        self.errors: List[ParseError] = []
        self.warnings: List[str] = []
    
    def record_error(self, error: ParseError):
        """Enregistre une erreur de parsing"""
        self.errors.append(error)
    
    def record_warning(self, warning: str, position: Position):
        """Enregistre un avertissement"""
        self.warnings.append(f"Warning at {position.line}:{position.column}: {warning}")


class DSLLanguageFeature(Enum):
    """Fonctionnalités du langage DSL supportées"""
    AGENT_DEFINITION = "agent_definition"
    RULE_ENGINE = "rule_engine"
    BUSINESS_LOGIC = "business_logic"
    COMPLIANCE = "compliance"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    IMPORTS = "imports"
    VERSIONING = "versioning"
    PRICING_MODELS = "pricing_models"
    MULTI_TENANCY = "multi_tenancy"


@dataclass
class DSLParser:
    """
    Parser DSL personnalisé pour les définitions d'agents MicroAgents
    
    Supporte une grammaire flexible avec validation en temps réel,
    génération d'AST et support d'auto-complétion.
    """
    
    # Configuration
    supported_features: List[DSLLanguageFeature] = field(default_factory=lambda: [
        DSLLanguageFeature.AGENT_DEFINITION,
        DSLLanguageFeature.RULE_ENGINE,
        DSLLanguageFeature.BUSINESS_LOGIC,
        DSLLanguageFeature.COMPLIANCE,
        DSLLanguageFeature.TESTING,
        DSLLanguageFeature.DOCUMENTATION,
        DSLLanguageFeature.IMPORTS,
        DSLLanguageFeature.VERSIONING,
        DSLLanguageFeature.PRICING_MODELS,
    ])
    
    # Version compatibility
    dsl_version: str = "1.0.0"
    min_compatible_version: str = "0.9.0"
    
    # Parser state
    state: ParserState = field(default_factory=ParserState)
    
    def __post_init__(self):
        """Initialisation après création"""
        self.lexer = DSLLexer()
        self.keywords = self._initialize_keywords()
        self.operators = self._initialize_operators()
        self.type_system = self._initialize_type_system()
        
    def _initialize_keywords(self) -> Dict[str, str]:
        """Initialise les mots-clés du DSL"""
        return {
            'agent': 'AGENT_KW',
            'type': 'TYPE_KW',
            'capabilities': 'CAPABILITIES_KW',
            'inputs': 'INPUTS_KW',
            'outputs': 'OUTPUTS_KW',
            'rules': 'RULES_KW',
            'business_value': 'BUSINESS_VALUE_KW',
            'calculator': 'CALCULATOR_KW',
            'parameters': 'PARAMETERS_KW',
            'dependencies': 'DEPENDENCIES_KW',
            'configuration': 'CONFIGURATION_KW',
            'compliance': 'COMPLIANCE_KW',
            'tests': 'TESTS_KW',
            'documentation': 'DOCUMENTATION_KW',
            'import': 'IMPORT_KW',
            'version': 'VERSION_KW',
            'requires': 'REQUIRES_KW',
            'if': 'IF_KW',
            'then': 'THEN_KW',
            'else': 'ELSE_KW',
            'and': 'AND_KW',
            'or': 'OR_KW',
            'not': 'NOT_KW',
            'in': 'IN_KW',
            'for': 'FOR_KW',
            'foreach': 'FOREACH_KW',
            'alert': 'ALERT_KW',
            'validate': 'VALIDATE_KW',
            'recommend': 'RECOMMEND_KW',
            'schedule': 'SCHEDULE_KW',
            'retry': 'RETRY_KW',
            'timeout': 'TIMEOUT_KW',
            'async': 'ASYNC_KW',
        }
    
    def _initialize_operators(self) -> Dict[str, str]:
        """Initialise les opérateurs du DSL"""
        return {
            '>': 'GT',
            '<': 'LT',
            '>=': 'GTE',
            '<=': 'LTE',
            '==': 'EQ',
            '!=': 'NEQ',
            '+': 'PLUS',
            '-': 'MINUS',
            '*': 'MULT',
            '/': 'DIV',
            '%': 'MOD',
            '=': 'ASSIGN',
            ':': 'COLON',
            ',': 'COMMA',
            '.': 'DOT',
            '[': 'LBRACKET',
            ']': 'RBRACKET',
            '{': 'LBRACE',
            '}': 'RBRACE',
            '(': 'LPAREN',
            ')': 'RPAREN',
        }
    
    def _initialize_type_system(self) -> Dict[str, List[str]]:
        """Initialise le système de types"""
        return {
            'scalar': ['string', 'int', 'float', 'bool', 'datetime', 'duration'],
            'complex': ['list', 'dict', 'set', 'tuple', 'object'],
            'business': ['currency', 'percentage', 'ratio', 'score'],
            'special': ['any', 'void', 'error', 'stream'],
        }
    
    def parse(self, source: str, source_path: Optional[str] = None) -> ASTNode:
        """
        Parse le code source DSL et retourne l'AST
        
        Args:
            source: Code source DSL
            source_path: Chemin du fichier source (optionnel)
            
        Returns:
            ASTNode: Arbre de syntaxe abstraite
            
        Raises:
            ParseError: Si des erreurs de syntaxe empêchent le parsing
        """
        try:
            # Reset state
            self.state = ParserState()
            
            # Tokenization
            tokens = self.lexer.tokenize(source)
            
            # Parse tokens into AST
            ast = self._parse_tokens(tokens, source_path)
            
            # Validate AST
            self._validate_ast(ast)
            
            # Check for errors
            if self.state.errors:
                error_messages = "\n".join(str(e) for e in self.state.errors)
                raise ParseError(
                    f"Parsing failed with {len(self.state.errors)} error(s):\n{error_messages}",
                    Position(1, 1)
                )
            
            return ast
            
        except LexerError as e:
            raise ParseError(f"Lexer error: {e}", Position(1, 1))
        except Exception as e:
            raise ParseError(f"Unexpected error during parsing: {e}", Position(1, 1))
    
    def _parse_tokens(self, tokens: List[DSLToken], source_path: Optional[str]) -> ASTNode:
        """Parse les tokens en AST"""
        # Consomme les tokens pour construire l'AST
        ast_nodes: List[ASTNode] = []
        index = 0
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'KEYWORD' and token.value == 'import':
                import_stmt = self._parse_import(tokens, index)
                ast_nodes.append(import_stmt)
                index = import_stmt.end_position.index if hasattr(import_stmt, 'end_position') else index + 1
                
            elif token.type == 'KEYWORD' and token.value == 'version':
                version_constraint = self._parse_version_constraint(tokens, index)
                self.state.version_constraints[version_constraint.package] = version_constraint
                index = version_constraint.end_position.index if hasattr(version_constraint, 'end_position') else index + 1
                
            elif token.type == 'KEYWORD' and token.value == 'agent':
                agent_def = self._parse_agent_definition(tokens, index)
                ast_nodes.append(agent_def)
                index = agent_def.end_position.index if hasattr(agent_def, 'end_position') else index + 1
                
            elif token.type == 'COMMENT':
                # Traite les commentaires de documentation
                if token.value.startswith('///') or token.value.startswith('/**'):
                    doc_block = self._parse_documentation_block(tokens, index)
                    if doc_block:
                        ast_nodes.append(doc_block)
                index += 1
                
            else:
                # Token non reconnu en position initiale
                if token.type not in ['WHITESPACE', 'NEWLINE']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' at start of definition",
                        token.position,
                        "Expected 'agent', 'import', or 'version' keyword"
                    ))
                index += 1
        
        # Crée le nœud racine
        root = ASTNode(
            node_type='ROOT',
            position=Position(1, 1),
            children=ast_nodes,
            metadata={
                'source_path': source_path,
                'dsl_version': self.dsl_version,
                'imports': self.state.imports,
                'version_constraints': self.state.version_constraints,
                'errors': self.state.errors,
                'warnings': self.state.warnings,
            }
        )
        
        return root
    
    def _parse_import(self, tokens: List[DSLToken], start_index: int) -> ImportStatement:
        """Parse une déclaration d'import"""
        start_token = tokens[start_index]
        index = start_index + 1
        
        # Vérifie le format d'import
        if index >= len(tokens) or tokens[index].type != 'STRING':
            self.state.record_error(ParseError(
                "Expected import path after 'import' keyword",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return ImportStatement(
                path="",
                alias=None,
                position=start_token.position,
                end_position=start_token.position
            )
        
        path_token = tokens[index]
        path = path_token.value.strip('"\'')
        alias = None
        index += 1
        
        # Vérifie un alias optionnel
        if index < len(tokens) and tokens[index].value == 'as':
            index += 1
            if index < len(tokens) and tokens[index].type == 'IDENTIFIER':
                alias = tokens[index].value
                index += 1
        
        # Crée la déclaration d'import
        import_stmt = ImportStatement(
            path=path,
            alias=alias,
            position=start_token.position,
            end_position=tokens[index - 1].position if index > start_index + 1 else path_token.position
        )
        
        # Enregistre l'import dans l'état
        self.state.imports.append(import_stmt)
        
        return import_stmt
    
    def _parse_version_constraint(self, tokens: List[DSLToken], start_index: int) -> VersionConstraint:
        """Parse une contrainte de version"""
        start_token = tokens[start_index]
        index = start_index + 1
        
        # Vérifie le nom du package
        if index >= len(tokens) or tokens[index].type != 'IDENTIFIER':
            self.state.record_error(ParseError(
                "Expected package name after 'version' keyword",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return VersionConstraint(
                package="",
                constraint="",
                position=start_token.position,
                end_position=start_token.position
            )
        
        package = tokens[index].value
        index += 1
        
        # Vérifie 'requires'
        if index >= len(tokens) or tokens[index].value != 'requires':
            self.state.record_error(ParseError(
                "Expected 'requires' keyword",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return VersionConstraint(
                package=package,
                constraint="",
                position=start_token.position,
                end_position=start_token.position
            )
        
        index += 1
        
        # Vérifie la contrainte de version
        if index >= len(tokens) or tokens[index].type != 'STRING':
            self.state.record_error(ParseError(
                "Expected version constraint string",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return VersionConstraint(
                package=package,
                constraint="",
                position=start_token.position,
                end_position=start_token.position
            )
        
        constraint = tokens[index].value.strip('"\'')
        index += 1
        
        return VersionConstraint(
            package=package,
            constraint=constraint,
            position=start_token.position,
            end_position=tokens[index - 1].position
        )
    
    def _parse_agent_definition(self, tokens: List[DSLToken], start_index: int) -> AgentDefinition:
        """Parse une définition d'agent complète"""
        start_token = tokens[start_index]
        index = start_index + 1
        
        # Vérifie le nom de l'agent
        if index >= len(tokens) or tokens[index].type != 'IDENTIFIER':
            self.state.record_error(ParseError(
                "Expected agent name after 'agent' keyword",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return AgentDefinition(
                name="",
                position=start_token.position,
                end_position=start_token.position
            )
        
        agent_name = tokens[index].value
        self.state.current_agent = agent_name
        index += 1
        
        # Vérifie ':'
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after agent name",
                tokens[index].position if index < len(tokens) else start_token.position
            ))
            return AgentDefinition(
                name=agent_name,
                position=start_token.position,
                end_position=start_token.position
            )
        
        index += 1
        
        # Parse le corps de l'agent
        agent_type = None
        capabilities: List[Capability] = []
        inputs: List[InputDefinition] = []
        outputs: List[OutputDefinition] = []
        rules: List[RuleDefinition] = []
        business_value: Optional[BusinessValueDefinition] = None
        dependencies: List[Dependency] = []
        configuration: Optional[Configuration] = None
        compliance: List[ComplianceRequirement] = []
        tests: List[TestSpec] = []
        documentation: Optional[DocumentationBlock] = None
        
        while index < len(tokens):
            token = tokens[index]
            
            # Fin de la définition d'agent
            if token.type == 'NEWLINE' and self._is_at_block_end(tokens, index):
                break
            
            # Parse les sections de l'agent
            if token.type == 'KEYWORD':
                if token.value == 'type':
                    agent_type, index = self._parse_type_section(tokens, index)
                elif token.value == 'capabilities':
                    capabilities, index = self._parse_capabilities_section(tokens, index)
                elif token.value == 'inputs':
                    inputs, index = self._parse_inputs_section(tokens, index)
                elif token.value == 'outputs':
                    outputs, index = self._parse_outputs_section(tokens, index)
                elif token.value == 'rules':
                    rules, index = self._parse_rules_section(tokens, index)
                elif token.value == 'business_value':
                    business_value, index = self._parse_business_value_section(tokens, index)
                elif token.value == 'dependencies':
                    dependencies, index = self._parse_dependencies_section(tokens, index)
                elif token.value == 'configuration':
                    configuration, index = self._parse_configuration_section(tokens, index)
                elif token.value == 'compliance':
                    compliance, index = self._parse_compliance_section(tokens, index)
                elif token.value == 'tests':
                    tests, index = self._parse_tests_section(tokens, index)
                elif token.value == 'documentation':
                    documentation, index = self._parse_documentation_section(tokens, index)
                else:
                    self.state.record_error(ParseError(
                        f"Unexpected keyword '{token.value}' in agent definition",
                        token.position,
                        "Expected one of: type, capabilities, inputs, outputs, rules, business_value, dependencies, configuration, compliance, tests, documentation"
                    ))
                    index += 1
            else:
                # Ignore les tokens non-keyword sauf erreur manifeste
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'DEDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in agent definition",
                        token.position
                    ))
                index += 1
        
        # Crée la définition d'agent
        agent_def = AgentDefinition(
            name=agent_name,
            agent_type=agent_type,
            capabilities=capabilities,
            inputs=inputs,
            outputs=outputs,
            rules=rules,
            business_value=business_value,
            dependencies=dependencies,
            configuration=configuration,
            compliance=compliance,
            tests=tests,
            documentation=documentation,
            position=start_token.position,
            end_position=tokens[index - 1].position if index > start_index else start_token.position
        )
        
        self.state.current_agent = None
        return agent_def
    
    def _parse_type_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[str, int]:
        """Parse la section 'type' d'un agent"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'type'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return "unknown", index
        
        index += 1
        
        if index >= len(tokens) or tokens[index].type != 'IDENTIFIER':
            self.state.record_error(ParseError(
                "Expected agent type identifier",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return "unknown", index
        
        agent_type = tokens[index].value
        index += 1
        
        return agent_type, index
    
    def _parse_capabilities_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[Capability], int]:
        """Parse la section 'capabilities'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'capabilities'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        # Parse une liste de capacités
        if index >= len(tokens) or tokens[index].value != '[':
            self.state.record_error(ParseError(
                "Expected '[' for capabilities list",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        capabilities: List[Capability] = []
        
        while index < len(tokens) and tokens[index].value != ']':
            token = tokens[index]
            
            if token.type == 'IDENTIFIER' or token.type == 'STRING':
                capability_name = token.value.strip('"\'') if token.type == 'STRING' else token.value
                capabilities.append(Capability(
                    name=capability_name,
                    description="",
                    position=token.position
                ))
            
            # Vérifie la virgule de séparation
            if index + 1 < len(tokens) and tokens[index + 1].value == ',':
                index += 1
            
            index += 1
        
        if index >= len(tokens) or tokens[index].value != ']':
            self.state.record_error(ParseError(
                "Expected ']' to close capabilities list",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
        else:
            index += 1
        
        return capabilities, index
    
    def _parse_inputs_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[InputDefinition], int]:
        """Parse la section 'inputs'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'inputs'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        inputs: List[InputDefinition] = []
        
        # Parse chaque ligne d'input
        while index < len(tokens):
            token = tokens[index]
            
            # Fin de la section
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            # Parse une définition d'input
            if token.type == 'IDENTIFIER':
                input_name = token.value
                index += 1
                
                # Vérifie ':'
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        f"Expected ':' after input name '{input_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                # Parse le type
                if index >= len(tokens) or tokens[index].type not in ['IDENTIFIER', 'STRING']:
                    self.state.record_error(ParseError(
                        f"Expected type for input '{input_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                input_type = tokens[index].value
                index += 1
                
                # Parse la valeur par défaut optionnelle
                default_value = None
                if index < len(tokens) and tokens[index].value == '=':
                    index += 1
                    if index < len(tokens):
                        default_token = tokens[index]
                        if default_token.type in ['STRING', 'NUMBER', 'IDENTIFIER', 'BOOLEAN']:
                            default_value = self._parse_literal(default_token)
                            index += 1
                
                # Parse la description optionnelle
                description = ""
                if index < len(tokens) and tokens[index].type == 'STRING':
                    description = tokens[index].value.strip('"\'')
                    index += 1
                
                inputs.append(InputDefinition(
                    name=input_name,
                    data_type=input_type,
                    default_value=default_value,
                    description=description,
                    position=token.position
                ))
            else:
                # Token non reconnu
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in inputs section",
                        token.position
                    ))
                index += 1
        
        return inputs, index
    
    def _parse_outputs_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[OutputDefinition], int]:
        """Parse la section 'outputs' (similaire à inputs)"""
        # Implémentation similaire à _parse_inputs_section
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'outputs'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        outputs: List[OutputDefinition] = []
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'IDENTIFIER':
                output_name = token.value
                index += 1
                
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        f"Expected ':' after output name '{output_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                if index >= len(tokens) or tokens[index].type not in ['IDENTIFIER', 'STRING']:
                    self.state.record_error(ParseError(
                        f"Expected type for output '{output_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                output_type = tokens[index].value
                index += 1
                
                description = ""
                if index < len(tokens) and tokens[index].type == 'STRING':
                    description = tokens[index].value.strip('"\'')
                    index += 1
                
                outputs.append(OutputDefinition(
                    name=output_name,
                    data_type=output_type,
                    description=description,
                    position=token.position
                ))
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in outputs section",
                        token.position
                    ))
                index += 1
        
        return outputs, index
    
    def _parse_rules_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[RuleDefinition], int]:
        """Parse la section 'rules' avec support de règles conditionnelles"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'rules'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        rules: List[RuleDefinition] = []
        current_rule: Optional[RuleDefinition] = None
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            # Règle simple: if condition then action
            if token.value == 'if':
                condition, action, index = self._parse_rule_expression(tokens, index)
                
                rule = RuleDefinition(
                    name=f"rule_{len(rules) + 1}",
                    condition=condition,
                    action=action,
                    position=token.position
                )
                rules.append(rule)
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in rules section, expected 'if'",
                        token.position
                    ))
                index += 1
        
        return rules, index
    
    def _parse_rule_expression(self, tokens: List[DSLToken], start_index: int) -> Tuple[str, str, int]:
        """Parse une expression de règle conditionnelle"""
        index = start_index
        condition_parts = []
        action_parts = []
        
        # Parse la condition
        index += 1  # Skip 'if'
        in_condition = True
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.value == 'then':
                in_condition = False
                index += 1
                continue
            
            if in_condition:
                condition_parts.append(token.value)
            else:
                # Fin de l'action
                if token.type == 'NEWLINE':
                    break
                action_parts.append(token.value)
            
            index += 1
        
        condition = ' '.join(condition_parts)
        action = ' '.join(action_parts)
        
        return condition, action, index
    
    def _parse_business_value_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[Optional[BusinessValueDefinition], int]:
        """Parse la section 'business_value'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'business_value'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return None, index
        
        index += 1
        
        calculator = None
        parameters = {}
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'KEYWORD' and token.value == 'calculator':
                index += 1
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        "Expected ':' after 'calculator'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                if index >= len(tokens) or tokens[index].type != 'IDENTIFIER':
                    self.state.record_error(ParseError(
                        "Expected calculator name",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                calculator = tokens[index].value
                index += 1
                
            elif token.type == 'KEYWORD' and token.value == 'parameters':
                index += 1
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        "Expected ':' after 'parameters'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                parameters, index = self._parse_parameters(tokens, index)
                
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in business_value section",
                        token.position
                    ))
                index += 1
        
        if calculator is None:
            self.state.record_warning(
                "Business value calculator not specified",
                tokens[start_index].position
            )
        
        business_value_def = BusinessValueDefinition(
            calculator=calculator,
            parameters=parameters,
            position=tokens[start_index].position
        ) if calculator else None
        
        return business_value_def, index
    
    def _parse_parameters(self, tokens: List[DSLToken], start_index: int) -> Tuple[Dict[str, Any], int]:
        """Parse un dictionnaire de paramètres"""
        index = start_index
        parameters = {}
        
        while index < len(tokens):
            token = tokens[index]
            
            # Fin des paramètres
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'IDENTIFIER':
                param_name = token.value
                index += 1
                
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        f"Expected ':' after parameter name '{param_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                if index >= len(tokens):
                    self.state.record_error(ParseError(
                        f"Expected value for parameter '{param_name}'",
                        tokens[index-1].position
                    ))
                    continue
                
                value_token = tokens[index]
                param_value = self._parse_literal(value_token)
                parameters[param_name] = param_value
                index += 1
                
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in parameters",
                        token.position
                    ))
                index += 1
        
        return parameters, index
    
    def _parse_literal(self, token: DSLToken) -> Any:
        """Parse un token littéral en valeur Python"""
        if token.type == 'STRING':
            return token.value.strip('"\'')
        elif token.type == 'NUMBER':
            try:
                if '.' in token.value:
                    return float(token.value)
                else:
                    return int(token.value)
            except ValueError:
                return token.value
        elif token.type == 'BOOLEAN':
            return token.value.lower() == 'true'
        elif token.type == 'IDENTIFIER':
            return token.value
        else:
            return str(token.value)
    
    def _parse_dependencies_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[Dependency], int]:
        """Parse la section 'dependencies'"""
        # Implémentation similaire aux autres sections
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'dependencies'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        dependencies: List[Dependency] = []
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            # Parse une dépendance
            if token.type == 'IDENTIFIER':
                dep_name = token.value
                version = None
                index += 1
                
                # Version optionnelle
                if index < len(tokens) and tokens[index].value == '@':
                    index += 1
                    if index < len(tokens) and tokens[index].type == 'STRING':
                        version = tokens[index].value.strip('"\'')
                        index += 1
                
                dependencies.append(Dependency(
                    name=dep_name,
                    version=version,
                    position=token.position
                ))
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in dependencies section",
                        token.position
                    ))
                index += 1
        
        return dependencies, index
    
    def _parse_configuration_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[Optional[Configuration], int]:
        """Parse la section 'configuration'"""
        # Implémentation similaire à _parse_parameters
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'configuration'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return None, index
        
        index += 1
        
        config_dict = {}
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'IDENTIFIER':
                config_name = token.value
                index += 1
                
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        f"Expected ':' after configuration key '{config_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                if index >= len(tokens):
                    self.state.record_error(ParseError(
                        f"Expected value for configuration '{config_name}'",
                        tokens[index-1].position
                    ))
                    continue
                
                value_token = tokens[index]
                config_value = self._parse_literal(value_token)
                config_dict[config_name] = config_value
                index += 1
                
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in configuration section",
                        token.position
                    ))
                index += 1
        
        if not config_dict:
            return None, index
        
        config = Configuration(
            settings=config_dict,
            position=tokens[start_index].position
        )
        
        return config, index
    
    def _parse_compliance_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[ComplianceRequirement], int]:
        """Parse la section 'compliance'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'compliance'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        compliance_reqs: List[ComplianceRequirement] = []
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'IDENTIFIER':
                standard = token.value
                index += 1
                
                version = None
                if index < len(tokens) and tokens[index].value == '@':
                    index += 1
                    if index < len(tokens) and tokens[index].type == 'STRING':
                        version = tokens[index].value.strip('"\'')
                        index += 1
                
                controls = []
                if index < len(tokens) and tokens[index].value == '(':
                    index += 1
                    while index < len(tokens) and tokens[index].value != ')':
                        if tokens[index].type == 'IDENTIFIER':
                            controls.append(tokens[index].value)
                        index += 1
                    
                    if index < len(tokens) and tokens[index].value == ')':
                        index += 1
                
                compliance_reqs.append(ComplianceRequirement(
                    standard=standard,
                    version=version,
                    controls=controls,
                    position=token.position
                ))
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in compliance section",
                        token.position
                    ))
                index += 1
        
        return compliance_reqs, index
    
    def _parse_tests_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[List[TestSpec], int]:
        """Parse la section 'tests'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'tests'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return [], index
        
        index += 1
        
        tests: List[TestSpec] = []
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'IDENTIFIER':
                test_name = token.value
                index += 1
                
                if index >= len(tokens) or tokens[index].value != ':':
                    self.state.record_error(ParseError(
                        f"Expected ':' after test name '{test_name}'",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                # Parse les inputs du test
                inputs = {}
                while index < len(tokens) and tokens[index].value != '=>':
                    if tokens[index].type == 'IDENTIFIER':
                        input_name = tokens[index].value
                        index += 1
                        
                        if index >= len(tokens) or tokens[index].value != ':':
                            self.state.record_error(ParseError(
                                f"Expected ':' after input name '{input_name}' in test",
                                tokens[index].position if index < len(tokens) else token.position
                            ))
                            continue
                        
                        index += 1
                        
                        if index >= len(tokens):
                            self.state.record_error(ParseError(
                                f"Expected value for input '{input_name}'",
                                tokens[index-1].position
                            ))
                            continue
                        
                        value_token = tokens[index]
                        input_value = self._parse_literal(value_token)
                        inputs[input_name] = input_value
                        index += 1
                    else:
                        index += 1
                
                # Vérifie '=>'
                if index >= len(tokens) or tokens[index].value != '=>':
                    self.state.record_error(ParseError(
                        "Expected '=>' in test definition",
                        tokens[index].position if index < len(tokens) else token.position
                    ))
                    continue
                
                index += 1
                
                # Parse les outputs attendus
                expected_outputs = {}
                while index < len(tokens) and tokens[index].type != 'NEWLINE':
                    if tokens[index].type == 'IDENTIFIER':
                        output_name = tokens[index].value
                        index += 1
                        
                        if index >= len(tokens) or tokens[index].value != ':':
                            self.state.record_error(ParseError(
                                f"Expected ':' after output name '{output_name}' in test",
                                tokens[index].position if index < len(tokens) else token.position
                            ))
                            continue
                        
                        index += 1
                        
                        if index >= len(tokens):
                            self.state.record_error(ParseError(
                                f"Expected value for output '{output_name}'",
                                tokens[index-1].position
                            ))
                            continue
                        
                        value_token = tokens[index]
                        output_value = self._parse_literal(value_token)
                        expected_outputs[output_name] = output_value
                        index += 1
                    else:
                        index += 1
                
                tests.append(TestSpec(
                    name=test_name,
                    inputs=inputs,
                    expected_outputs=expected_outputs,
                    position=token.position
                ))
            else:
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT', 'COMMENT']:
                    self.state.record_error(ParseError(
                        f"Unexpected token '{token.value}' in tests section",
                        token.position
                    ))
                index += 1
        
        return tests, index
    
    def _parse_documentation_section(self, tokens: List[DSLToken], start_index: int) -> Tuple[Optional[DocumentationBlock], int]:
        """Parse la section 'documentation'"""
        index = start_index + 1
        
        if index >= len(tokens) or tokens[index].value != ':':
            self.state.record_error(ParseError(
                "Expected ':' after 'documentation'",
                tokens[index].position if index < len(tokens) else tokens[start_index].position
            ))
            return None, index
        
        index += 1
        
        content_parts = []
        
        while index < len(tokens):
            token = tokens[index]
            
            if token.type == 'NEWLINE' and self._is_at_section_end(tokens, index):
                break
            
            if token.type == 'STRING':
                content_parts.append(token.value.strip('"\''))
            else:
                # Conserve le texte brut pour la documentation
                if token.type not in ['WHITESPACE', 'NEWLINE', 'INDENT']:
                    content_parts.append(token.value)
            
            index += 1
        
        if not content_parts:
            return None, index
        
        documentation = DocumentationBlock(
            content=' '.join(content_parts),
            language="markdown",
            position=tokens[start_index].position
        )
        
        return documentation, index
    
    def _parse_documentation_block(self, tokens: List[DSLToken], start_index: int) -> Optional[DocumentationBlock]:
        """Parse un bloc de documentation (commentaires spéciaux)"""
        token = tokens[start_index]
        
        if not token.value.startswith('///') and not token.value.startswith('/**'):
            return None
        
        # Extraction du contenu du commentaire
        content = token.value
        if content.startswith('///'):
            content = content[3:].strip()
        elif content.startswith('/**'):
            content = content[3:-2].strip() if content.endswith('*/') else content[3:].strip()
        
        return DocumentationBlock(
            content=content,
            language="markdown",
            position=token.position
        )
    
    def _is_at_section_end(self, tokens: List[DSLToken], index: int) -> bool:
        """Détermine si on est à la fin d'une section"""
        # Regarde les tokens suivants pour voir si on change de section
        lookahead = index + 1
        while lookahead < len(tokens) and tokens[lookahead].type in ['WHITESPACE', 'NEWLINE']:
            lookahead += 1
        
        if lookahead >= len(tokens):
            return True
        
        # Si le prochain token non-blanc est un mot-clé de section ou la fin du bloc
        next_token = tokens[lookahead]
        if next_token.type == 'KEYWORD':
            section_keywords = {
                'type', 'capabilities', 'inputs', 'outputs', 'rules',
                'business_value', 'dependencies', 'configuration',
                'compliance', 'tests', 'documentation'
            }
            return next_token.value in section_keywords
        
        return False
    
    def _is_at_block_end(self, tokens: List[DSLToken], index: int) -> bool:
        """Détermine si on est à la fin d'un bloc (définition d'agent)"""
        # Vérifie le niveau d'indentation
        lookahead = index
        while lookahead < len(tokens) and tokens[lookahead].type in ['WHITESPACE', 'NEWLINE']:
            lookahead += 1
        
        if lookahead >= len(tokens):
            return True
        
        # Si le prochain token est à un niveau d'indentation inférieur
        # ou est un nouveau mot-clé de haut niveau
        next_token = tokens[lookahead]
        if next_token.type == 'DEDENT':
            return True
        
        # Vérifie si c'est le début d'une nouvelle définition
        if next_token.type == 'KEYWORD' and next_token.value in ['agent', 'import', 'version']:
            return True
        
        return False
    
    def _validate_ast(self, ast: ASTNode):
        """Valide l'AST généré"""
        # Validation de base de la structure
        if ast.node_type != 'ROOT':
            self.state.record_error(ParseError(
                "AST root must be of type 'ROOT'",
                ast.position
            ))
        
        # Valide chaque nœud enfant
        for child in ast.children:
            self._validate_node(child)
    
    def _validate_node(self, node: ASTNode):
        """Valide un nœud AST individuel"""
        if isinstance(node, AgentDefinition):
            self._validate_agent_definition(node)
        elif isinstance(node, ImportStatement):
            self._validate_import_statement(node)
        elif isinstance(node, VersionConstraint):
            self._validate_version_constraint(node)
    
    def _validate_agent_definition(self, agent: AgentDefinition):
        """Valide une définition d'agent"""
        # Vérifie que l'agent a un nom
        if not agent.name:
            self.state.record_error(ParseError(
                "Agent must have a name",
                agent.position
            ))
        
        # Vérifie que le type d'agent est valide
        valid_agent_types = {
            'detector', 'analyzer', 'optimizer', 'remediator',
            'validator', 'auditor', 'predictor', 'integrator',
            'controller', 'governor', 'scheduler'
        }
        
        if agent.agent_type and agent.agent_type not in valid_agent_types:
            self.state.record_warning(
                f"Agent type '{agent.agent_type}' is not a standard type. "
                f"Valid types are: {', '.join(sorted(valid_agent_types))}",
                agent.position
            )
        
        # Vérifie que les inputs ont des noms uniques
        input_names = [inp.name for inp in agent.inputs]
        if len(input_names) != len(set(input_names)):
            self.state.record_error(ParseError(
                "Input names must be unique within an agent",
                agent.position
            ))
        
        # Vérifie que les outputs ont des noms uniques
        output_names = [out.name for out in agent.outputs]
        if len(output_names) != len(set(output_names)):
            self.state.record_error(ParseError(
                "Output names must be unique within an agent",
                agent.position
            ))
        
        # Vérifie les types de données
        for input_def in agent.inputs:
            self._validate_data_type(input_def.data_type, input_def.position)
        
        for output_def in agent.outputs:
            self._validate_data_type(output_def.data_type, output_def.position)
        
        # Vérifie les références dans les règles
        for rule in agent.rules:
            self._validate_rule_references(rule, agent)
    
    def _validate_data_type(self, data_type: str, position: Position):
        """Valide un type de données"""
        # Types scalaires de base
        basic_types = {
            'string', 'int', 'float', 'bool', 'datetime', 'duration',
            'currency', 'percentage', 'ratio', 'score'
        }
        
        # Vérifie les types de liste
        if data_type.startswith('list[') and data_type.endswith(']'):
            inner_type = data_type[5:-1]
            self._validate_data_type(inner_type, position)
        elif data_type.startswith('dict[') and ']' in data_type:
            # dict[key_type, value_type]
            inner_types = data_type[5:-1].split(',')
            if len(inner_types) == 2:
                key_type, value_type = inner_types
                self._validate_data_type(key_type.strip(), position)
                self._validate_data_type(value_type.strip(), position)
        elif data_type not in basic_types and data_type != 'any':
            self.state.record_warning(
                f"Data type '{data_type}' is not a standard type",
                position
            )
    
    def _validate_rule_references(self, rule: RuleDefinition, agent: AgentDefinition):
        """Valide les références dans une règle"""
        # Extrait les identifiants de la condition et de l'action
        import re
        
        # Recherche les références aux inputs
        input_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b'
        tokens = re.findall(input_pattern, rule.condition + ' ' + rule.action)
        
        input_names = set(inp.name for inp in agent.inputs)
        
        for token in tokens:
            # Vérifie si c'est un input référencé
            if token in input_names:
                continue
            
            # Vérifie si c'est un mot-clé
            if token in self.keywords:
                continue
            
            # Vérifie si c'est un opérateur
            if token in self.operators:
                continue
            
            # Vérifie si c'est une fonction
            if token in ['alert', 'validate', 'recommend', 'schedule', 'retry']:
                continue
            
            # Sinon, c'est peut-être une référence non déclarée
            self.state.record_warning(
                f"Reference to '{token}' in rule might be undefined",
                rule.position
            )
    
    def _validate_import_statement(self, import_stmt: ImportStatement):
        """Valide une déclaration d'import"""
        if not import_stmt.path:
            self.state.record_error(ParseError(
                "Import statement must have a path",
                import_stmt.position
            ))
        
        # Vérifie le format du chemin
        if not re.match(r'^[a-zA-Z0-9_./-]+$', import_stmt.path):
            self.state.record_error(ParseError(
                f"Invalid import path format: {import_stmt.path}",
                import_stmt.position
            ))
    
    def _validate_version_constraint(self, constraint: VersionConstraint):
        """Valide une contrainte de version"""
        if not constraint.package:
            self.state.record_error(ParseError(
                "Version constraint must have a package name",
                constraint.position
            ))
        
        # Vérifie le format de la contrainte
        version_pattern = r'^[<>!=~^]*\d+\.\d+(\.\d+)?([a-zA-Z0-9-+.]*)?$'
        if not re.match(version_pattern, constraint.constraint):
            self.state.record_error(ParseError(
                f"Invalid version constraint format: {constraint.constraint}",
                constraint.position
            ))
    
    def get_suggestions(self, partial_input: str, position: Position) -> List[str]:
        """
        Retourne des suggestions d'auto-complétion
        
        Args:
            partial_input: Input partiel à compléter
            position: Position courante dans le code
            
        Returns:
            Liste de suggestions
        """
        suggestions = []
        
        # Suggestions basées sur le contexte
        lines = partial_input.split('\n')
        current_line = lines[position.line - 1][:position.column]
        
        # Suggestions de mots-clés en début de ligne
        if not current_line.strip() or current_line.strip().startswith('#'):
            suggestions.extend(self.keywords.keys())
        
        # Suggestions après 'agent'
        elif 'agent' in current_line and ':' not in current_line:
            suggestions.append("<agent_name>")
        
        # Suggestions dans les sections
        elif any(section in current_line for section in ['type:', 'capabilities:', 'inputs:', 'outputs:']):
            if 'type:' in current_line:
                suggestions.extend(['detector', 'analyzer', 'optimizer', 'remediator', 'validator'])
            elif 'capabilities:' in current_line:
                suggestions.extend(['cost_analysis', 'anomaly_detection', 'security_scan', 'compliance_check'])
        
        return sorted(set(suggestions))
    
    def check_version_compatibility(self, dsl_version: str) -> bool:
        """
        Vérifie la compatibilité de version
        
        Args:
            dsl_version: Version du DSL à vérifier
            
        Returns:
            True si compatible
        """
        from packaging import version
        
        try:
            current = version.parse(self.dsl_version)
            other = version.parse(dsl_version)
            min_version = version.parse(self.min_compatible_version)
            
            return other >= min_version and other.major == current.major
        except:
            return False
    
    def get_parse_errors(self) -> List[ParseError]:
        """Retourne les erreurs de parsing enregistrées"""
        return self.state.errors
    
    def get_parse_warnings(self) -> List[str]:
        """Retourne les avertissements de parsing"""
        return self.state.warnings
    
    def format_error_message(self, error: ParseError, source: str) -> str:
        """
        Formate un message d'erreur avec le contexte
        
        Args:
            error: Erreur à formater
            source: Code source original
            
        Returns:
            Message d'erreur formaté
        """
        lines = source.split('\n')
        error_line = lines[error.position.line - 1] if error.position.line <= len(lines) else ""
        
        message = f"\n{'='*60}\n"
        message += f"Parse Error: {error.message}\n"
        message += f"Line {error.position.line}, Column {error.position.column}\n"
        message += f"{'-'*40}\n"
        message += f"{error_line}\n"
        message += " " * (error.position.column - 1) + "^\n"
        
        if error.suggestion:
            message += f"Suggestion: {error.suggestion}\n"
        
        message += f"{'='*60}\n"
        
        return message


# Fonction utilitaire pour parser depuis un fichier
def parse_file(file_path: str) -> ASTNode:
    """
    Parse un fichier DSL
    
    Args:
        file_path: Chemin vers le fichier DSL
        
    Returns:
        ASTNode: Arbre de syntaxe abstraite
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    parser = DSLParser()
    return parser.parse(source, file_path)


# Fonction utilitaire pour parser une chaîne DSL
def parse_string(source: str) -> ASTNode:
    """
    Parse une chaîne DSL
    
    Args:
        source: Code source DSL
        
    Returns:
        ASTNode: Arbre de syntaxe abstraite
    """
    parser = DSLParser()
    return parser.parse(source)


# Exemple d'utilisation
if __name__ == "__main__":
    # Exemple de code DSL
    example_dsl = """
agent CostAnomalyDetector:
  type: detector
  capabilities: [cost_analysis, anomaly_detection]
  inputs:
    - monthly_cost: float
    - cost_baseline: float
  rules:
    - if monthly_cost > cost_baseline * 1.2 then alert(severity: high)
  outputs:
    - anomaly_score: float
    - confidence: float
  business_value:
    calculator: CostAvoidanceCalculator
    parameters:
      hourly_engineer_cost: 150
"""
    
    try:
        parser = DSLParser()
        ast = parser.parse(example_dsl)
        
        print("✓ Parsing réussi!")
        print(f"  Nombre de nœuds: {len(ast.children)}")
        
        for child in ast.children:
            if isinstance(child, AgentDefinition):
                print(f"  Agent: {child.name}")
                print(f"    Type: {child.agent_type}")
                print(f"    Inputs: {len(child.inputs)}")
                print(f"    Outputs: {len(child.outputs)}")
                print(f"    Règles: {len(child.rules)}")
        
        # Affiche les avertissements
        warnings = parser.get_parse_warnings()
        if warnings:
            print("\n⚠ Avertissements:")
            for warning in warnings:
                print(f"  {warning}")
        
    except ParseError as e:
        print(f"✗ Erreur de parsing: {e}")